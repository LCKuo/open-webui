import logging

from fastapi import APIRouter, Depends, HTTPException, status

from open_webui.utils.auth import get_verified_user
from open_webui.utils.interact_billing import InteractBillingClient, is_billing_enabled

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/wallet")
async def get_billing_wallet(user=Depends(get_verified_user)):
    if not is_billing_enabled():
        return {
            "ok": True,
            "enabled": False,
            "wallet": None,
        }

    try:
        wallet = await InteractBillingClient().wallet(user)
    except HTTPException:
        raise
    except Exception as e:
        log.warning("Failed to fetch billing wallet: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to fetch company portal token balance.",
        )

    return {
        "ok": True,
        "enabled": True,
        "wallet": wallet,
    }

