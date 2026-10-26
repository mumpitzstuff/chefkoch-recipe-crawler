import re
from scrapy.exceptions import DropItem, NotConfigured
from .util import load_existing_recipe_ids


class DuplicatesPipeline:
    """Drops duplicate recipe items based on their recipe ID or URL."""

    @classmethod
    def from_crawler(cls, crawler):
        if not crawler.settings.getbool('DEDUPLICATION_ENABLED', True):
            raise NotConfigured("Deduplication disabled via DEDUPLICATION_ENABLED=False")
        existing_file = crawler.settings.get('EXISTING_RECIPES_FILE', 'recipes.jsonl')
        return cls(existing_file=existing_file)

    def __init__(self, existing_file='recipes.jsonl'):
        self.seen_ids = load_existing_recipe_ids(existing_file)

    def process_item(self, item, spider):
        url = item.get('url') or ''
        match = re.search(r'/rezepte/(\d+)/', url)
        recipe_id = match.group(1) if match else url

        if not recipe_id:
            return item

        if recipe_id in self.seen_ids:
            raise DropItem(f"Duplicate recipe dropped: {recipe_id}")

        self.seen_ids.add(recipe_id)
        return item
