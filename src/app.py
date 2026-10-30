import sentry_sdk
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.address import router as address_router
from routes.card import router as card_router
from routes.cart import router as cart_router
from routes.checkout import router as checkout_router
from routes.collection import router as collection_router
from routes.listing import router as listing_router
from routes.order import router as order_router
from routes.rating import router as rating_router
from routes.scan import router as scan_router
from routes.seller import router as seller_router
from routes.shipping import router as shipping_router
from routes.stripe_webhook import router as stripe_webhook_router
from routes.user import router as user_router
from routes.wants import router as wants_router
from utils.errors import register_error_handlers
from utils.rate_limit import RateLimitMiddleware
from utils.settings import settings

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        # Don't attach request bodies/IPs: requests carry addresses and tokens.
        send_default_pii=False,
        enable_logs=True,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )

app = FastAPI(title="MTG Marketplace API")
register_error_handlers(app)

# Middleware added last runs first: CORS wraps everything, so even 429
# responses carry CORS headers and the browser can read them.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    # Auth is a bearer token, not cookies, so credentials mode isn't needed.
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Retry-After"],
    max_age=600,
)

v1 = APIRouter(prefix="/v1")
for router in (user_router, address_router, card_router, collection_router,
               listing_router, order_router, rating_router, shipping_router, seller_router, cart_router,
               checkout_router, stripe_webhook_router, scan_router, wants_router):
    v1.include_router(router)
app.include_router(v1)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
