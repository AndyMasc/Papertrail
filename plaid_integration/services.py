import logging

import plaid
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest

from .plaid_client import client as plaid_client

logger = logging.getLogger(__name__)


def public_token_exchange(public_token: str) -> tuple[str, str]:
    try:
        request = ItemPublicTokenExchangeRequest(public_token=public_token)
        response = plaid_client.item_public_token_exchange(request)

        access_token = response["access_token"]
        item_id = response["item_id"]

        return access_token, item_id

    except plaid.ApiException as e:
        logger.error("Plaid API error during exchange: %s", e)
        raise
    except Exception:
        logger.exception("Unexpected error in public_token_exchange")
        raise
