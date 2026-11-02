import re
from scrapy.exceptions import DropItem, NotConfigured
from .util import load_existing_recipe_ids

RECIPE_URL_RE = re.compile(r'/rezepte/(\d+)/')


class DuplicatesPipeline:
    """
    Safety-net pipeline that drops duplicate recipe items by recipe ID.

    The spider already skips fetching known recipe URLs via process_recipe_request(),
    so this pipeline mainly catches duplicates that appear within a single crawl run
    (e.g. the same recipe linked from multiple category pages).

    Category pages never yield items, so they pass through untouched.
    """

    @classmethod
    def from_crawler(cls, crawler):
        if not crawler.settings.getbool('DEDUPLICATION_ENABLED', True):
            raise NotConfigured("Deduplication disabled via DEDUPLICATION_ENABLED=False")
        existing_file = crawler.settings.get('EXISTING_RECIPES_FILE', 'recipes.jsonl')
        instance = cls(existing_file=existing_file)
        instance.crawler = crawler
        return instance

    def __init__(self, existing_file='recipes.jsonl'):
        # Pre-load IDs from a previous run's output file so we also catch
        # cross-run duplicates that slipped past the spider-level filter.
        self.seen_ids = load_existing_recipe_ids(existing_file)
        self.crawler = None

    def process_item(self, item, spider=None):
        spider = spider or (self.crawler.spider if self.crawler else None)
        url = item.get('url') or ''
        match = RECIPE_URL_RE.search(url)

        if not match:
            # Not a recipe item (e.g. category page somehow yielded an item);
            # let it pass through without touching the seen-IDs set.
            if spider:
                spider.logger.debug('DuplicatesPipeline: no recipe ID in URL, passing through: %s', url)
            return item

        recipe_id = match.group(1)

        if recipe_id in self.seen_ids:
            if spider:
                spider.logger.info('DuplicatesPipeline: dropping duplicate recipe %s (%s)', recipe_id, url)
            raise DropItem(f"Duplicate recipe: {recipe_id}")

        self.seen_ids.add(recipe_id)
        return item
