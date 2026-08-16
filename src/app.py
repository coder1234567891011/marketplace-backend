from fastapi import FastAPI, Depends, Header, HTTPException
import sentry_sdk

from auth.auth import get_current_user
from routes.user import router as user_router
from routes.order import router as order_router
from routes.rating import router as rating_router
from routes.listing import router as listing_router
from routes.collection import router as collection_router
from routes.address import router as address_router

sentry_sdk.init(
    dsn="https://98f0d2e62420546cd35670cfee7f8997@o4511898216562688.ingest.us.sentry.io/4511898225672192",
    # Add data like request headers and IP for users,
    # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
    send_default_pii=True,
    # Enable sending logs to Sentry
    enable_logs=True,
    # Set traces_sample_rate to 1.0 to capture 100%
    # of transactions for tracing.
    traces_sample_rate=1.0,
)

app = FastAPI()
app.include_router(user_router)
app.include_router(order_router)
app.include_router(rating_router)
app.include_router(listing_router)
app.include_router(collection_router)
app.include_router(address_router)

@app.get("/me")
def read_me(user: dict = Depends(get_current_user)):
    return {"user_id": user["sub"], "email": user.get("email")}

@app.get("/sentry-debug")
async def trigger_error():
    division_by_zero = 1 / 0

@app.get("/")
async def root():
    return {"message": "Hello World"}