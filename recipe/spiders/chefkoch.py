import re
from scrapy.spiders import CrawlSpider, Rule, Request
from scrapy.linkextractors import LinkExtractor

from ..util import text, html, strip_hash_qs, apply_priority


class ChefkochSpider(CrawlSpider):
    name = 'chefkoch'
    allowed_domains = ['chefkoch.de']
    start_urls = ['https://www.chefkoch.de/rezepte/']

    rules = (
        # category pages
        Rule(
            LinkExtractor(allow=(r'/rs/s\d+/'))
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
            process_request=apply_priority(10)
        )
    )

    def start_requests(self):
        recipe_urls = self.settings.get('RECIPE_URLS')
        if recipe_urls is not None:
            self.logger.info('Seeding from file %s' % recipe_urls)
            for line in open(recipe_urls):
                yield Request(url=line.strip(), callback=self.parse_recipe, dont_filter=True)
        else:
            for r in super().start_requests(): yield r

    def parse_recipe(self, response):
        meta = {
            text(c.css('.recipe-meta-property-group__title').get()): text(c.css('.recipe-meta-property-group__value').get())
            for c in response.css('.recipe-meta-property-group__cell')
        }

        rating_avg_m = re.search(r'[\d,.]+', text(response.css('.ds-rating__avg, .ds-rating-avg strong').get()) or '')
        rating_count_m = re.search(r'\d+', text(response.css('.ds-rating__votes, .ds-rating-count').get()) or '')
        comment_count_m = re.search(r'\d+', text(response.css('.comments-link__link, .rds-comment-ctn-btn').get()) or '')

        author_url = response.css('.recipe-meta-property__link::attr(href), .recipe-author .bi-profile::attr(href)').get()
        if author_url:
            author_url = response.urljoin(author_url)

        return {
            'url': response.url,
            'url_fingerprint': self.crawler.request_fingerprinter.fingerprint(response.request).hex(),
            'name': text(response.css('h1::text').get()),
            'image_url': response.css('meta[property="og:image"]::attr(content)').get(),
            'description': text(response.css('header p::text, .recipe-text').get()),
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
        }

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
