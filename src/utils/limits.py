"""Per-user write limits, in one place so they're easy to tune."""
from utils.rate_limit import rate_limit

HOUR = 3600

PROFILE_CREATE = rate_limit("profile_create", 5, HOUR)
PROFILE_UPDATE = rate_limit("profile_update", 30, HOUR)
ADDRESSES = rate_limit("addresses", 30, HOUR)
COLLECTIONS = rate_limit("collections", 60, HOUR)
COLLECTION_ITEMS = rate_limit("collection_items", 300, HOUR)
LISTING_CREATE = rate_limit("listing_create", 100, HOUR)
LISTING_EDIT = rate_limit("listing_edit", 300, HOUR)
RATINGS = rate_limit("ratings", 20, HOUR)
COLLECTION_IMPORT = rate_limit("collection_import", 30, HOUR)
SHIPPING = rate_limit("shipping", 30, HOUR)
SELLER_LINKS = rate_limit("seller_links", 30, HOUR)
CART = rate_limit("cart", 600, HOUR)
CHECKOUT = rate_limit("checkout", 20, HOUR)
ORDER_ACTIONS = rate_limit("order_actions", 300, HOUR)
SCANS = rate_limit("scans", 1000, HOUR)
WANTS = rate_limit("wants", 600, HOUR)
WANTS_OPTIMIZE = rate_limit("wants_optimize", 60, HOUR)
ISSUES = rate_limit("issues", 120, HOUR)
REPORTS = rate_limit("reports", 30, HOUR)
