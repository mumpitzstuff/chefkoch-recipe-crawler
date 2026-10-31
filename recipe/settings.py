
BOT_NAME = "recipe"
SPIDER_MODULES = ["recipe.spiders"]

# Pagination links only appear during browsing through the pages, needs depth.
DEPTH_LIMIT = 200

# Per-request download delays (applied via request.meta['download_delay'] in the spider).
# Recipe pages use DOWNLOAD_DELAY; category/search pages use CATEGORY_DOWNLOAD_DELAY.
DOWNLOAD_DELAY = 2.0
CATEGORY_DOWNLOAD_DELAY = 30.0
# Disable ±50% jitter so our explicit per-request delays are applied exactly.
# (request.meta['download_delay'] is never randomized by Scrapy regardless,
# but this keeps the global fallback consistent too.)
RANDOMIZE_DOWNLOAD_DELAY = False

# Mimic a real browser to avoid bot detection.
# The server blocks requests based on headers, not IP (same IP works in a browser).
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/130.0.0.0 Safari/537.36'
)

# Send browser-like headers with every request.
DEFAULT_REQUEST_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Upgrade-Insecure-Requests': '1',
}

# Enable cookies — sites use session cookies as part of bot detection.
COOKIES_ENABLED = True
# Useful for development, and we want to keep the raw sources too.
HTTPCACHE_ENABLED = True
# Speedup when crawling from cache (at the expense of memory use)
CONCURRENT_ITEMS = 1500
REACTOR_THREADPOOL_MAXSIZE = 50

# Deduplication of recipes (skips already scraped recipes from recipes.jsonl and drops duplicates)
DEDUPLICATION_ENABLED = True

RETRY_HTTP_CODES = [429]

DOWNLOADER_MIDDLEWARES = {
    'scrapy.downloadermiddlewares.retry.RetryMiddleware': None,
    'recipe.requestretry.TooManyRequestsRetryMiddleware': 500,
}

ITEM_PIPELINES = {
    'recipe.pipelines.DuplicatesPipeline': 300,
}
