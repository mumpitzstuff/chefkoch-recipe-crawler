import re
import json
from scrapy.spiders import CrawlSpider, Rule, Request
from scrapy.linkextractors import LinkExtractor

from ..util import text, html, strip_hash_qs, load_existing_recipe_ids


class ChefkochSpider(CrawlSpider):
    name = 'chefkoch'
    allowed_domains = ['chefkoch.de']
    start_urls = ['https://www.chefkoch.de/rezepte/']

    rules = (
        # category pages — crawled slowly to avoid triggering rate limits
        Rule(
            LinkExtractor(allow=(r'/rs/s\d+/')),
            process_request='process_category_request'
        ),
        # recipes
        Rule(
            LinkExtractor(
                allow=(r'www\.chefkoch\.de/rezepte/\d+'),
                # Clean up URLs parts for tracking, as this pollutes the cache and could give duplicates.
                # - hash is often included for tracking the session
                # - query string may be present for tracking plus recipe traffic source
                process_value=strip_hash_qs,
                # omit plus recipes, for which we have no access
                restrict_css='*:not([data-vars-payed-content-type=plus_recipe]) > a'
            ),
            callback='parse_recipe',
            process_request='process_recipe_request'
        )
    )

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        spider.deduplication_enabled = crawler.settings.getbool('DEDUPLICATION_ENABLED', True)
        if spider.deduplication_enabled:
            existing_file = crawler.settings.get('EXISTING_RECIPES_FILE', 'recipes.jsonl')
            spider.existing_recipe_ids = load_existing_recipe_ids(existing_file)
            if spider.existing_recipe_ids:
                spider.logger.info('Loaded %d existing recipe IDs from %s - they will be skipped' % (len(spider.existing_recipe_ids), existing_file))
        else:
            spider.existing_recipe_ids = set()
            spider.logger.info('Deduplication is disabled via DEDUPLICATION_ENABLED=False.')
        return spider

    def process_category_request(self, request, response):
        """Apply a generous delay to category/search pages to avoid rate limiting."""
        return request.replace(
            meta={**request.meta, 'download_delay': self.settings.getfloat('CATEGORY_DOWNLOAD_DELAY', 30.0)}
        )

    def process_recipe_request(self, request, response):
        match = re.search(r'/rezepte/(\d+)/', request.url)
        recipe_id = match.group(1) if match else None

        if self.deduplication_enabled and recipe_id and recipe_id in self.existing_recipe_ids:
            self.logger.debug(
                'Skipping already-known recipe %s (found on %s)', recipe_id, response.url
            )
            return None

        self.logger.debug(
            'Queuing recipe %s from %s', request.url, response.url
        )
        return request.replace(
            priority=10,
            meta={**request.meta, 'download_delay': self.settings.getfloat('DOWNLOAD_DELAY', 2.0)}
        )

    def start_requests(self):
        recipe_urls = self.settings.get('RECIPE_URLS')
        if recipe_urls is not None:
            self.logger.info('Seeding from file %s' % recipe_urls)
            with open(recipe_urls) as fh:
                for line in fh:
                    url = line.strip()
                    if self.deduplication_enabled:
                        match = re.search(r'/rezepte/(\d+)/', url)
                        if match and match.group(1) in self.existing_recipe_ids:
                            continue
                    yield Request(url=url, callback=self.parse_recipe)
        else:
            for r in super().start_requests(): yield r

    def parse_recipe(self, response):
        meta = {
            k: text(c.css('.recipe-meta-property-group__value').get())
            for c in response.css('.recipe-meta-property-group__cell')
            if (k := text(c.css('.recipe-meta-property-group__title').get())) is not None
        }

        rating_avg_m = re.search(r'[\d,.]+', text(response.css('.ds-rating__avg, .ds-rating-avg strong').get()) or '')
        rating_count_m = re.search(r'\d+', text(response.css('.ds-rating__votes, .ds-rating-count').get()) or '')
        comment_count_m = re.search(r'\d+', text(response.css('.comments-link__link, .rds-comment-ctn-btn').get()) or '')

        author_url = response.css('.recipe-meta-property__link::attr(href), .recipe-author .bi-profile::attr(href)').get()
        if author_url:
            author_url = response.urljoin(author_url)

        ld = self.get_ld_json_recipe(response)
        abstract = self.extract_abstract(response, ld)
        fallback_desc = (
            text(response.css('meta[name="description"]::attr(content)').get())
            or text(response.css('.recipe-intro, .recipe-text').xpath('string(.)').get())
        )
        description = text(ld.get('description')) or fallback_desc

        return {
            'url': response.url,
            'url_fingerprint': self.crawler.request_fingerprinter.fingerprint(response.request).hex(),
            'name': text(response.css('h1::text').get()),
            'image_url': response.css('meta[property="og:image"]::attr(content)').get(),
            'description': description or abstract,
            'abstract': abstract,
            'preparation_time': meta.get('Arbeitszeit') or text(response.css('.recipe-preptime::text').get()),
            'difficulty': meta.get('Schwierigkeit') or text(response.css('.recipe-difficulty::text').get()),
            'date': meta.get('Erstellt am') or text(response.css('.recipe-date::text').get()),
            'rating_avg': rating_avg_m.group(0) if rating_avg_m else None,
            'rating_count': rating_count_m.group(0) if rating_count_m else None,
            'comment_count': comment_count_m.group(0) if comment_count_m else None,
            'author_name': meta.get('Rezeptautor:in') or text(response.css('.recipe-author .bi-profile::attr(data-vars-bi-username)').get()),
            'author_url': author_url,
            'portions': response.css('.ds-quantity-control input[name=quantity]::attr(value), .ds-quantity-control__amount::text, .recipe-servings input[name=portionen]::attr(value)').get(),
            'ingredients': self.extract_ingredients(response),
            'preparation': self.extract_preparation(response),
            'durations': self.extract_durations(response),
            'tags': self.extract_tags(response),
            'nutrition': self.extract_nutrition(response, ld),
        }

    def get_ld_json_recipe(self, response):
        """Extract the Schema.org Recipe object from application/ld+json."""
        for script in response.xpath('//script[contains(@type, "ld+json")]'):
            s = script.xpath('string()').get()
            if not s:
                continue
            try:
                data = json.loads(s.strip())
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = data.get('@graph', [data])
                else:
                    items = []
                for item in items:
                    if isinstance(item, dict):
                        t = item.get('@type')
                        if t == 'Recipe' or (isinstance(t, list) and 'Recipe' in t):
                            return item
            except Exception:
                pass
        return {}

    def extract_abstract(self, response, ld):
        abstract = text(ld.get('abstract'))
        if abstract and abstract.lower() != 'null':
            return abstract

        for selector in (
            '.editorial-intro__text', '.recipe-intro', '.ds-recipe-intro'
        ):
            for node in response.css(selector):
                value = text(node.xpath('string(.)').get())
                if value and value.lower() not in {
                    'null', 'deine plus inhalte', 'meine plus inhalte'
                }:
                    return value

        return None

    def extract_ingredients(self, response):
        ingredients = []
        section = None
        for row in response.css('.ds-ingredients-table tr, .ingredients tr'):
            if row.css('th'):
                s = text(row.css('th').get())
                if s: s = s.strip(':')
                if s != '':
                    section = s
                continue
            if not row.css('td'):
                continue

            name = text(row.css('.ds-ingredients-table__ingredient-name').get())
            props = text(row.css('.ds-ingredients-table__ingredient-properties').get())

            if row.css('[data-testid="sponsored-ingredient-link"]'):
                props = None
                url = None
            else:
                url = row.css('td:last-child a::attr(href)').get()
                if url:
                    url = response.urljoin(url)

            if name:
                if props:
                    if props.startswith('(') and props.endswith(')'):
                        name = f"{name} {props}"
                    else:
                        name = f"{name} ({props})"
            else:
                name = text(row.css('td:last-child').get())

            ingredients.append({
                'section': section,
                'amount': text(row.css('td:first-child').get()),
                'name': name,
                'url': url
            })
        return ingredients

    def extract_preparation(self, response):
        steps = response.css('[data-testid="recipe-instruction"]::text, .instruction__text::text').getall()
        if not steps:
            steps = response.css('h2:contains("Zubereitung") + .ds-recipe-meta + .ds-box *::text').getall()
        return [s.strip() for s in steps if s.strip()]

    def extract_durations(self, response):
        durations = {}
        for cell in response.css('.ds-preparation-times__cell'):
            title = text(cell.css('.ds-preparation-times__title').get())
            val = text(cell.css('.ds-preparation-times__time').get())
            if title and val:
                durations[title] = val
        if not durations:
            for line in response.css('.ds-box > .ds-recipe-meta > *::text').getall():
                parts = line.strip().split(' ', 1)
                if len(parts) == 2:
                    durations[parts[0]] = parts[1]
        return durations

    def extract_tags(self, response):
        tags = response.css('.ds-filter-chip .ds-filter-chip__label::text, .recipe-tags .ds-tag::text').getall()
        return [text(s) for s in tags if text(s)]

    def extract_nutrition(self, response, ld):
        nutrition = self.extract_nuxt_nutrition(response)
        ld_nutrition = ld.get('nutrition') if isinstance(ld, dict) else None
        if isinstance(ld_nutrition, dict):
            ld_nutrition = [ld_nutrition]

        for values in ld_nutrition if isinstance(ld_nutrition, list) else []:
            if not isinstance(values, dict):
                continue
            basis = str(values.get('servingSize', '')).lower()
            label = 'pro 100 g' if '100' in basis else 'pro Portion'
            if label not in nutrition:
                nutrition[label] = {
                    key: str(value).strip()
                    for key, value in values.items()
                    if key not in {'@type', 'servingSize'} and value is not None
                }

        if not nutrition:
            label = text(response.css('.ds-tab-nav__link--active::text').get()) or 'Pro Portion'
            values = self.extract_nutrition_cells(response)
            if values:
                nutrition['pro 100 g' if '100' in label else 'pro Portion'] = values
        return nutrition

    def extract_nuxt_nutrition(self, response):
        script = response.css('#__NUXT_DATA__::text').get()
        if not script:
            return {}
        try:
            state = json.loads(script)
        except json.JSONDecodeError:
            return {}

        record = next((
            value for value in state
            if isinstance(value, dict) and 'per100g' in value
        ), None)
        if not record:
            return {}

        def decode(value):
            if isinstance(value, int) and 0 <= value < len(state):
                return decode_node(state[value])
            return value

        def decode_node(value):
            if isinstance(value, dict):
                return {key: decode(item) for key, item in value.items()}
            if isinstance(value, list):
                return [decode(item) for item in value]
            return value

        metadata = {
            'per100g', 'source', 'status', 'servingBasis',
            'servingCount', 'missingIngredientIds',
        }
        values = decode_node(record)
        portion = {
            key: str(value).strip()
            for key, value in values.items()
            if key not in metadata and value is not None
        }
        per_100g = values.get('per100g')
        nutrition = {'pro Portion': portion} if portion else {}
        if isinstance(per_100g, dict):
            nutrition['pro 100 g'] = {
                key: str(value).strip()
                for key, value in per_100g.items()
                if value is not None
            }
        return nutrition

    def extract_nutrition_cells(self, container):
        cells = {}
        for cell in container.css('.ds-nutrition__cell, .nutrition-card__partial, tr'):
            name = text(cell.css(
                '.ds-nutrition__title::text, .nutrition-card__title::text, th::text, td:first-child::text'
            ).get())
            value = text(cell.css(
                '.ds-nutrition__value::text, .nutrition-card__basis::text, td:last-child::text'
            ).get())
            if name and value:
                cells[name.rstrip(':')] = value
        return cells
