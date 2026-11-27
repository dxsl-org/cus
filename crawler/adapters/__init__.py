from crawler.adapters import commoncrawl, listing, oai_pmh, sitemap, tinhte, wayback, wordpress, xenforo

ADAPTERS = {
    "sitemap": sitemap,
    "listing": listing,
    "wordpress": wordpress,
    "xenforo": xenforo,
    "tinhte": tinhte,
    "oai_pmh": oai_pmh,
    "wayback": wayback,
    "commoncrawl": commoncrawl,
}
