from fastapi import FastAPI
import sentry_sdk

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

@app.get("/sentry-debug")
async def trigger_error():
    division_by_zero = 1 / 0