from fastapi import FastAPI, Depends, Header, HTTPException
import sentry_sdk
import jwt

from auth.auth import verify_supabase_jwt
from utils.settings import Settings

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
settings = Settings()

def get_current_user(authorization: str = Header(...)) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        return verify_supabase_jwt(token, settings)
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

@app.get("/me")
def read_me(user: dict = Depends(get_current_user)):
    return {"user_id": user["sub"], "email": user.get("email")}

@app.get("/sentry-debug")
async def trigger_error():
    division_by_zero = 1 / 0

@app.get("/")
async def root():
    return {"message": "Hello World"}