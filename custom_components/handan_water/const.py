"""Constants for the read-only Handan Water integration."""

from datetime import timedelta

DOMAIN = "handan_water"
NAME = "邯郸供水"
CONF_ACCOUNT = "account_number"
CONF_ALLOW_HTTP = "allow_insecure_http"
CONF_TRANSPORT = "transport"
CONF_HISTORY = "import_history"
CONF_PAYMENTS = "query_payments"
CONF_ANALYSIS = "query_analysis"
CONF_HISTORY_LIMIT = "history_attribute_limit"
DEFAULT_HISTORY_LIMIT = 24
UPDATE_INTERVAL = timedelta(days=1)
API_HOST = "www.hdwsc.com.cn"
API_PATH = "/adsWeChat/pt.action"
API_ID = "4YA45Q"
READ_SERVICES = frozenset({"200001", "200005", "200008", "200020"})
REQUEST_TIMEOUT = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
STORAGE_VERSION = 1
