CREATE ROLE scraper_api LOGIN PASSWORD 'scraper-api-dev';
CREATE ROLE scraper_worker LOGIN PASSWORD 'scraper-worker-dev';
GRANT CONNECT ON DATABASE article_scraper TO scraper_api, scraper_worker;
GRANT USAGE ON SCHEMA public TO scraper_api, scraper_worker;
