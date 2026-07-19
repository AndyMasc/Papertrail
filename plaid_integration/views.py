import json
import logging
from typing import Any

import plaid
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from plaid.model.country_code import CountryCode
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from rest_framework import authentication, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from records.models import Record

from .models import PlaidItem
from .plaid_client import client
from .services import public_token_exchange
from .tasks import sync_and_convert_for_item_task

logger: logging.Logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def plaid_connect_page(request: Request) -> HttpResponse:
    plaid_items = PlaidItem.objects.filter(user=request.user).prefetch_related("records")
    return render(request, "plaid/connect.html", {"plaid_items": plaid_items})


class CreateLinkTokenView(APIView):
    authentication_classes = [authentication.SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        try:
            request_obj = LinkTokenCreateRequest(
                user=LinkTokenCreateRequestUser(client_user_id=str(request.user.id)),
                client_name="Papertrail",
                products=[Products("transactions")],
                country_codes=[CountryCode("US")],
                language="en",
            )
            response = client.link_token_create(request_obj)
            return Response({"link_token": response["link_token"]})
        except plaid.ApiException as e:
            logger.exception("Link token creation failed for user %s", request.user)
            return Response({"error": str(e)}, status=400)


class PublicTokenExchange(APIView):
    authentication_classes = [authentication.SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        public_token: str | None = request.data.get("public_token")

        if not public_token:
            return Response({"error": "public_token is required"}, status=400)
        try:
            access_token, item_id = public_token_exchange(public_token)

            with transaction.atomic():
                PlaidItem.objects.create(
                    user=request.user,
                    item_id=item_id,
                    access_token=access_token,
                )
            return Response({"success": "Token exchange was successful."})
        except Exception:
            logger.exception("Failed to exchange public token for user %s", request.user)
            return Response({"error": "Failed to exchange token"}, status=400)


class PlaidStatusView(APIView):
    authentication_classes = [authentication.SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        plaid_items = PlaidItem.objects.filter(user=request.user).prefetch_related("records")

        return Response(
            {
                "connected": plaid_items.exists(),
                "items": [
                    {
                        "item_id": item.item_id,
                        "created_at": item.created_at.isoformat() if item.created_at else None,
                        "has_cursor": bool(item.next_cursor),
                        "record_count": len(item.records.all()),
                    }
                    for item in plaid_items
                ],
            }
        )


class SyncTransactionsView(APIView):
    authentication_classes = [authentication.SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        item_id: str | None = request.data.get("item_id")

        try:
            if item_id:
                plaid_item = PlaidItem.objects.get(user=request.user, item_id=item_id)
            else:
                plaid_item = PlaidItem.objects.filter(user=request.user).first()
                if not plaid_item:
                    raise PlaidItem.DoesNotExist
        except PlaidItem.DoesNotExist:
            return Response({"error": "No Plaid link found for the specified item"}, status=400)

        sync_and_convert_for_item_task.delay(plaid_item.id)
        return Response({"status": "sync_initiated"})


class DisconnectBankView(APIView):
    authentication_classes = [authentication.SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, item_id: str) -> Response:
        try:
            plaid_item = PlaidItem.objects.get(user=request.user, item_id=item_id)
        except PlaidItem.DoesNotExist:
            return Response({"error": "Bank connection not found"}, status=404)

        try:
            client.item_remove(ItemRemoveRequest(access_token=plaid_item.access_token))
        except plaid.ApiException:
            logger.exception("Failed to remove Plaid item %s from Plaid dashboard", item_id)

        plaid_item.delete()
        return Response({"success": "Bank disconnected"})


@csrf_exempt
@require_POST
def plaid_webhook(request: HttpRequest) -> HttpResponse:
    try:
        payload = json.loads(request.body)
    except (ValueError, TypeError):
        return HttpResponseBadRequest("Invalid JSON")

    webhook_type: str = payload.get("webhook_type", "")
    webhook_code: str = payload.get("webhook_code", "")
    item_id: str = payload.get("item_id", "")

    logger.info("Plaid webhook received: %s / %s for item %s", webhook_type, webhook_code, item_id)

    try:
        plaid_item = PlaidItem.objects.get(item_id=item_id)
    except PlaidItem.DoesNotExist:
        logger.warning("Webhook received for unknown item %s", item_id)
        return HttpResponse("OK")

    if webhook_code == "SYNC_UPDATES_AVAILABLE":
        sync_and_convert_for_item_task.delay(plaid_item.id)

    elif webhook_code in ("ITEM_LOGIN_REQUIRED", "ITEM_REQUIRES_UPDATE", "PENDING_EXPIRATION"):
        logger.warning("Item %s requires manual user intervention: %s", item_id, webhook_code)

    elif webhook_code == "ERROR":
        error: dict[str, Any] = payload.get("error", {})
        logger.error("Plaid context error for item %s: %s", item_id, error.get("error_message"))

    elif webhook_code == "TRANSACTIONS_REMOVED":
        txns: list[str] = payload.get("removed_transactions", [])
        with transaction.atomic():
            Record.objects.filter(plaid_transaction_id__in=txns).delete()

    return HttpResponse("OK")
