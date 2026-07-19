import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

import jwt
import plaid
import requests
from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone as tz
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
from plaid.model.institutions_get_by_id_request_options import InstitutionsGetByIdRequestOptions
from plaid.model.item_get_request import ItemGetRequest
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

PLAID_JWKS_URL = "https://plaid.com/auth/v1/webhook_public_key"
_jwks_cache: dict[str, Any] = {}
_jwks_fetched_at: float | None = None


def _get_plaid_jwk(kid: str, max_age: int = 3600) -> dict[str, Any] | None:
    global _jwks_cache, _jwks_fetched_at
    now = datetime.now(timezone.utc).timestamp()
    if not _jwks_fetched_at or (now - _jwks_fetched_at) > max_age:
        try:
            resp = requests.get(PLAID_JWKS_URL, timeout=10)
            resp.raise_for_status()
            keys = resp.json().get("keys", [])
            _jwks_cache = {k["kid"]: k for k in keys}
            _jwks_fetched_at = now
        except Exception:
            logger.exception("Failed to fetch Plaid JWKS")
            return None
    return _jwks_cache.get(kid)


def verify_plaid_webhook(body: bytes, plaid_verification: str | None) -> bool:
    if not plaid_verification:
        logger.warning("Missing Plaid-Verification header")
        return False
    try:
        unverified = jwt.decode(plaid_verification, options={"verify_signature": False})
        kid = unverified.get("kid", "")
        jwk = _get_plaid_jwk(kid)
        if not jwk:
            logger.warning("No Plaid JWK found for kid=%s", kid)
            return False

        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        claims = jwt.decode(
            plaid_verification,
            public_key,
            algorithms=["RS256"],
            options={
                "verify_iat": True,
                "verify_exp": True,
            },
        )
        body_hash = hashlib.sha256(body).hexdigest()
        if claims.get("request_body_sha256") != body_hash:
            logger.warning("Plaid webhook body hash mismatch")
            return False
        return True
    except jwt.PyJWTError as e:
        logger.warning("Plaid webhook JWT verification failed: %s", e)
        return False


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
                webhook=settings.PLAID_WEBHOOK_URL,
            )
            response = client.link_token_create(request_obj)
            return Response({"link_token": response["link_token"]})
        except plaid.ApiException as e:
            logger.exception("Link token creation failed for user %s", request.user)
            return Response({"error": str(e)}, status=400)


class CreateUpdateLinkTokenView(APIView):
    authentication_classes = [authentication.SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, item_id: str) -> Response:
        try:
            plaid_item = PlaidItem.objects.get(user=request.user, item_id=item_id)
        except PlaidItem.DoesNotExist:
            return Response({"error": "Bank connection not found"}, status=404)

        try:
            request_obj = LinkTokenCreateRequest(
                user=LinkTokenCreateRequestUser(client_user_id=str(request.user.id)),
                client_name="Papertrail",
                products=[Products("transactions")],
                country_codes=[CountryCode("US")],
                language="en",
                webhook=settings.PLAID_WEBHOOK_URL,
                access_token=plaid_item.access_token,
            )
            response = client.link_token_create(request_obj)
            return Response({"link_token": response["link_token"]})
        except plaid.ApiException as e:
            logger.exception("Update link token creation failed for item %s", item_id)
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
            accounts_data: list[dict[str, str]] = request.data.get("accounts", [])
            institution_name = "Bank Account"

            try:
                item_resp = client.item_get(ItemGetRequest(access_token=access_token))
                item_data = item_resp if isinstance(item_resp, dict) else item_resp.to_dict()
                inst_id = item_data.get("item", {}).get("institution_id", "")
                if inst_id:
                    inst_req = InstitutionsGetByIdRequest(
                        institution_id=inst_id,
                        country_codes=[CountryCode("US")],
                        options=InstitutionsGetByIdRequestOptions(include_optional_metadata=False),
                    )
                    inst_resp = client.institutions_get_by_id(inst_req)
                    inst_data = inst_resp if isinstance(inst_resp, dict) else inst_resp.to_dict()
                    institution_name = inst_data.get("institution", {}).get("name", "Bank Account")
            except Exception:
                logger.warning("Failed to fetch institution name for item %s", item_id)

            with transaction.atomic():
                plaid_item = PlaidItem.objects.create(
                    user=request.user,
                    item_id=item_id,
                    access_token=access_token,
                    institution_name=institution_name,
                    accounts_data=accounts_data,
                )
            sync_and_convert_for_item_task.delay(plaid_item.id)
            return Response({"success": "Bank linked successfully! Syncing transactions…"})
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
                        "account_name": item.institution_name,
                        "accounts_data": item.accounts_data,
                        "last_error_code": item.last_error_code,
                        "last_error_message": item.last_error_message,
                        "last_error_at": item.last_error_at.isoformat() if item.last_error_at else None,
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

    if not verify_plaid_webhook(request.body, request.headers.get("Plaid-Verification")):
        logger.warning("Plaid webhook verification failed for %s", payload.get("item_id"))

    webhook_type: str = payload.get("webhook_type", "")
    webhook_code: str = payload.get("webhook_code", "")
    item_id: str = payload.get("item_id", "")

    logger.info(
        "Plaid webhook received: %s / %s for item %s", webhook_type, webhook_code, item_id
    )

    try:
        plaid_item = PlaidItem.objects.get(item_id=item_id)
    except PlaidItem.DoesNotExist:
        logger.warning("Webhook received for unknown item %s", item_id)
        return HttpResponse("OK")

    if webhook_code == "SYNC_UPDATES_AVAILABLE":
        sync_and_convert_for_item_task.delay(plaid_item.id)

    elif webhook_code in ("ITEM_LOGIN_REQUIRED", "ITEM_REQUIRES_UPDATE", "PENDING_EXPIRATION"):
        logger.warning("Item %s requires manual user intervention: %s", item_id, webhook_code)
        PlaidItem.objects.filter(id=plaid_item.id).update(
            last_error_code=webhook_code,
            last_error_message=payload.get("error", {}).get(
                "error_message", "User action required"
            ),
            last_error_at=tz.now(),
        )

    elif webhook_code == "ERROR":
        error: dict[str, Any] = payload.get("error", {})
        logger.error("Plaid error for item %s: %s", item_id, error.get("error_message"))
        PlaidItem.objects.filter(id=plaid_item.id).update(
            last_error_code=error.get("error_code", webhook_code),
            last_error_message=error.get("error_message", "Unknown error"),
            last_error_at=tz.now(),
        )

    elif webhook_code == "TRANSACTIONS_REMOVED":
        txns: list[str] = payload.get("removed_transactions", [])
        with transaction.atomic():
            Record.objects.filter(plaid_transaction_id__in=txns).delete()

    return HttpResponse("OK")
