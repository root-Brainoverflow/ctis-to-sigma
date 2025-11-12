
import asyncio
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Set, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright

# Optional: improve date parsing if dateutil is available
try:
    from dateutil import parser as du_parser  # type: ignore
    HAVE_DATEUTIL = True
except Exception:
    HAVE_DATEUTIL = False


class LinkCollector:
    def __init__(self, base_url_file: str = "base_url.txt", output_file: str = "urls.txt"):
        self.base_url_file = base_url_file
        self.output_file = output_file
        self.collected_links: Set[str] = set()
        self.links_by_source: Dict[str, List[str]] = {}
        self.link_dates: Dict[str, datetime] = {}  # per-link publish datetime
        self.max_concurrent_pages = 5
        self.max_concurrent_sites = 3
        self.max_concurrent_date_fetches = 8

    async def collect_links(self):
        """Read URLs from base_url.txt and collect report links across multiple pages."""
        base_urls = self._read_base_urls()
        if not base_urls:
            print("No URLs found in base_url.txt.")
            return

        print(f"Number of sites to process: {len(base_urls)}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            browser_context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "Chrome/120 Safari/537.36"
                ),
                java_script_enabled=True,
            )
            # block heavy/noisy resources for faster, less flaky loads
            await browser_context.route("**/*", self._route_blocker)

            try:
                semaphore = asyncio.Semaphore(self.max_concurrent_sites)
                tasks = []
                for i, base_url in enumerate(base_urls):
                    task = self._process_site_with_semaphore(
                        semaphore, browser_context, base_url, i + 1, len(base_urls)
                    )
                    tasks.append(task)
                await asyncio.gather(*tasks)
            except KeyboardInterrupt:
                print("\nUser terminated the program.")
            finally:
                await browser_context.close()
                await browser.close()

        self._save_urls_by_section()
        print(f"\nSaved to: {Path(self.output_file).absolute()}")
        print(f"Saved a total of {len(self.collected_links)} links to {self.output_file}.")
        self._summarize_links()

    async def _route_blocker(self, route):
        """Abort useless resources and trackers to reduce timeouts."""
        try:
            req = route.request
            rtype = req.resource_type
            if rtype in ("image", "media", "font"):
                return await route.abort()
            url = req.url
            noisy = (
                "googletagmanager", "google-analytics", "doubleclick",
                "facebook", "twitter", "hotjar", "adservice", "optimizely"
            )
            if any(x in url for x in noisy):
                return await route.abort()
            return await route.continue_()
        except Exception:
            return await route.continue_()

    async def _process_site_with_semaphore(self, semaphore, browser_context, base_url, site_num, total_sites):
        """Process one site while respecting a semaphore limit."""
        async with semaphore:
            await self._process_single_site(browser_context, base_url, site_num, total_sites)

    async def _process_single_site(self, browser_context, base_url, site_num, total_sites):
        """Process a single site, enrich dates for new links, and store by source."""
        print(f"\n[{site_num}/{total_sites}] Processing: {base_url}")

        initial_collected_links = set(self.collected_links)

        site_type = await self._detect_site_type(browser_context, base_url)
        print(f"[{site_num}] Site type: {site_type}")

        if site_type == "load_more":
            await self._collect_load_more_site(browser_context, base_url, site_num)
        else:
            await self._collect_paginated_site_parallel(browser_context, base_url, site_num)

        # Keep only new links from this site
        new_links_for_this_site: List[str] = []
        current_domain = urlparse(base_url).netloc.lower()
        for link in self.collected_links:
            if link not in initial_collected_links:
                if self._is_link_from_domain(link, current_domain):
                    new_links_for_this_site.append(link)

        # Enrich publish dates for new links (skip ones already set from listing scrape)
        await self._enrich_dates(browser_context, new_links_for_this_site, site_num)

        self.links_by_source[base_url] = new_links_for_this_site

        print(f"[{site_num}] Done: collected {len(new_links_for_this_site)} new links (from this site)")
        print(f"[{site_num}] Total links collected so far: {len(self.collected_links)}")

    async def _enrich_dates(self, browser_context, links: List[str], site_num: int):
        """Populate self.link_dates for given links using URL hints and page metadata."""
        sem = asyncio.Semaphore(self.max_concurrent_date_fetches)

        async def work(url: str):
            if url in self.link_dates:
                return
            # 1) quick from URL path
            dt = self._date_from_url(url)
            if not dt:
                # 2) open article page to find published date
                dt = await self._extract_date_from_page(browser_context, url, site_num)
            if dt:
                self.link_dates[url] = dt

        await asyncio.gather(*(self._with_sem(sem, work, u) for u in links))

    async def _with_sem(self, sem: asyncio.Semaphore, fn, *args, **kwargs):
        async with sem:
            return await fn(*args, **kwargs)

    def _date_from_url(self, url: str) -> Optional[datetime]:
        """Infer date from URL path if it encodes a date."""
        u = url.lower()

        # /YYYY/MM/DD/
        m = re.search(r"/(20\d{2})/([01]?\d)/([0-3]?\d)/", u)
        if m:
            y, mo, d = map(int, m.groups())
            return self._safe_dt(y, mo, d)

        # /YYYY-MM-DD/ or /YYYY.MM.DD/
        m = re.search(r"/(20\d{2})[-./]([01]?\d)[-./]([0-3]?\d)/", u)
        if m:
            y, mo, d = map(int, m.groups())
            return self._safe_dt(y, mo, d)

        return None

    async def _extract_date_from_page(self, browser_context, url: str, site_num: int) -> Optional[datetime]:
        """Open article page and try JSON-LD, meta, <time>, and headers."""
        page = await browser_context.new_page()
        header_dt = None
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)

            # 0) HTTP header: Last-Modified
            try:
                if resp:
                    last_mod = await resp.header_value("last-modified")  # type: ignore[attr-defined]
                    dt = self._parse_date_string(last_mod)
                    if dt:
                        header_dt = dt
            except Exception:
                pass

            # 1) JSON-LD (BlogPosting / NewsArticle)
            try:
                scripts = await page.locator('script[type="application/ld+json"]').all_inner_texts()
                for raw in scripts:
                    for obj in self._iter_jsonld_objects(raw):
                        for key in ("datePublished", "dateCreated", "uploadDate", "pubDate"):
                            if key in obj and obj[key]:
                                dt = self._parse_date_string(str(obj[key]))
                                if dt:
                                    return dt
                        if "mainEntity" in obj and isinstance(obj["mainEntity"], dict):
                            for key in ("datePublished", "dateCreated"):
                                val = obj["mainEntity"].get(key)
                                if val:
                                    dt = self._parse_date_string(str(val))
                                    if dt:
                                        return dt
            except Exception:
                pass

            # 2) meta tags
            meta_candidates = [
                'meta[property="article:published_time"]',
                'meta[name="pubdate"]',
                'meta[name="publishdate"]',
                'meta[name="date"]',
                'meta[name="timestamp"]',
                'meta[property="og:published_time"]',
                'meta[property="article:modified_time"]',
                'meta[property="og:updated_time"]',
            ]
            for sel in meta_candidates:
                try:
                    el = page.locator(sel).first
                    if await el.count():
                        val = await el.get_attribute("content")
                        dt = self._parse_date_string(val)
                        if dt:
                            return dt
                except Exception:
                    pass

            # 3) time elements and common date spans
            time_candidates = [
                "time[datetime]",
                "time.entry-date",
                "span.posted-on time",
                "span.post-meta time",
                "span.post-date",
                "div.post-meta time",
                "div.blog-post-meta time",
                "span.entry-date",
                "div.entry-meta time",
            ]
            for sel in time_candidates:
                try:
                    el = page.locator(sel).first
                    if await el.count():
                        attr = await el.get_attribute("datetime")
                        dt = self._parse_date_string(attr) if attr else None
                        if dt:
                            return dt
                        text = (await el.text_content() or "").strip()
                        dt = self._parse_date_string(text)
                        if dt:
                            return dt
                except Exception:
                    pass

            # 4) site-specific: Genians sometimes shows "25.10.28" near title
            domain = urlparse(url).netloc.lower()
            if "genians.co.kr" in domain:
                try:
                    near = page.locator(
                        ":text-matches('^\\d{2,4}[.\\-/]\\d{1,2}[.\\-/]\\d{1,2}$')"
                    ).first
                    if await near.count():
                        text = (await near.text_content() or "").strip()
                        dt = self._parse_date_string(text)
                        if dt:
                            return dt
                except Exception:
                    pass

            # 5) header fallback
            if header_dt:
                return header_dt

            # 6) scan raw HTML for datetime=
            html = await page.content()
            m = re.search(r'datetime="([^"]+)"', html, flags=re.IGNORECASE)
            if m:
                dt = self._parse_date_string(m.group(1))
                if dt:
                    return dt

            return None
        except Exception as e:
            print(f"  [*] Date parse failed for {url}: {e}")
            return None
        finally:
            await page.close()

    def _iter_jsonld_objects(self, raw: str):
        """Yield JSON objects from a LD+JSON script. Handles arrays and nested dicts safely."""
        try:
            data = json.loads(raw)
        except Exception:
            return []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    yield item
        elif isinstance(data, dict):
            yield data

    def _parse_date_string(self, s: Optional[str]) -> Optional[datetime]:
        """Parse various date string formats to naive datetime."""
        if not s:
            return None
        s = s.strip()

        # Prefer python-dateutil when available
        if HAVE_DATEUTIL:
            try:
                dt = du_parser.parse(s, fuzzy=True, dayfirst=False)
                return dt.replace(tzinfo=None)
            except Exception:
                pass

        # ISO-8601 like
        try:
            iso = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            return dt.replace(tzinfo=None)
        except Exception:
            pass

        # yyyy-mm-dd or yyyy/mm/dd or yyyy.mm.dd
        m = re.match(r"^(20\d{2})[-/.]([01]?\d)[-/.]([0-3]?\d)$", s)
        if m:
            y, mo, d = map(int, m.groups())
            return self._safe_dt(y, mo, d)

        # yy.mm.dd or yy-mm-dd (assume 2000s)
        m = re.match(r"^(\d{2})[-/.]([01]?\d)[-/.]([0-3]?\d)$", s)
        if m:
            y2, mo, d = map(int, m.groups())
            y = 2000 + y2
            return self._safe_dt(y, mo, d)

        # dd Month yyyy or Month dd, yyyy
        for fmt in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                continue

        return None

    def _safe_dt(self, y: int, mo: int, d: int) -> Optional[datetime]:
        try:
            return datetime(int(y), int(mo), int(d))
        except ValueError:
            return None

    def _is_link_from_domain(self, link: str, expected_domain: str) -> bool:
        """Check if a link belongs to the expected domain (allow sub/super domains by mapping)."""
        try:
            link_domain = urlparse(link).netloc.lower()
            expected_domain = expected_domain.lower()

            domain_mapping = {
                "asec.ahnlab.com": ["asec.ahnlab.com"],
                "www.fortinet.com": ["www.fortinet.com", "fortinet.com"],
                "fortinet.com": ["www.fortinet.com", "fortinet.com"],
                "research.checkpoint.com": ["research.checkpoint.com"],
                "checkpoint.com": ["research.checkpoint.com", "checkpoint.com"],
                "thedfirreport.com": ["thedfirreport.com"],
                "www.genians.co.kr": ["www.genians.co.kr", "genians.co.kr"],
                "genians.co.kr": ["www.genians.co.kr", "genians.co.kr"],
            }

            for domain_key, allowed_domains in domain_mapping.items():
                if domain_key in expected_domain:
                    return link_domain in allowed_domains

            return (
                expected_domain == link_domain
                or expected_domain in link_domain
                or link_domain in expected_domain
            )
        except Exception:
            return False

    def _read_base_urls(self) -> list:
        """Read URL list from base_url.txt."""
        try:
            with open(self.base_url_file, "r", encoding="utf-8") as f:
                urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
                return urls
        except FileNotFoundError:
            print(f"Could not find {self.base_url_file}.")
            return []

    async def _detect_site_type(self, browser_context, base_url: str) -> str:
        """Detect site type (load more vs pagination) from the landing page."""
        page = await browser_context.new_page()
        try:
            await page.goto(base_url, wait_until="domcontentloaded", timeout=10000)
            await page.wait_for_timeout(1000)

            load_more_patterns = [
                'button:has-text("Load more")',
                'button:has-text("Load More")',
                'button:has-text("Load more stories")',
                "button.btn",
                "button[data-loadmore-target]",
                ".load-more-button",
                "#load-more",
            ]

            for pattern in load_more_patterns:
                try:
                    elements = await page.locator(pattern).all()
                    for element in elements:
                        if await element.is_visible():
                            text = await element.text_content() or ""
                            if any(keyword in text.lower() for keyword in ["load more", "more stories", "load"]):
                                return "load_more"
                except Exception:
                    continue

            return "pagination"
        except Exception:
            return "pagination"
        finally:
            await page.close()

    async def _collect_load_more_site(self, browser_context, base_url: str, site_num: int) -> int:
        """Handle 'Load More' style sites by clicking the button multiple times and scraping links."""
        page = await browser_context.new_page()
        try:
            await page.goto(base_url, wait_until="domcontentloaded", timeout=10000)
            await page.wait_for_timeout(3000)

            initial_count = len(self.collected_links)
            click_count = 0
            max_clicks = 20
            consecutive_no_new = 0
            limit_click_count = 3

            # Initial page links
            initial_links = await self._extract_links(page)
            new_initial_links = initial_links - self.collected_links
            if new_initial_links:
                self.collected_links.update(new_initial_links)
                print(f"  [{site_num}] Initial page: +{len(new_initial_links)} links")
            else:
                print(f"  [{site_num}] No new links on the initial page")

            # Click loop
            while click_count < max_clicks and consecutive_no_new < limit_click_count:
                button_clicked = False
                button_patterns = [
                    'button:has-text("Load more")',
                    'button:has-text("Load More")',
                    "button.btn",
                    "button[data-loadmore-target]",
                    ".load-more-button",
                    "#load-more",
                ]
                for pattern in button_patterns:
                    try:
                        buttons = await page.locator(pattern).all()
                        for button in buttons:
                            if await button.is_visible():
                                await button.click()
                                # Wait briefly then for network idle, or at least for selector
                                await page.wait_for_timeout(400)
                                await page.wait_for_load_state("networkidle")
                                click_count += 1
                                button_clicked = True
                                break
                    except Exception:
                        continue
                    if button_clicked:
                        break

                if not button_clicked:
                    break

                links = await self._extract_links(page)
                new_links = links - self.collected_links
                if new_links:
                    self.collected_links.update(new_links)
                    print(f"  [{site_num}] Click #{click_count}: +{len(new_links)} links")
                    consecutive_no_new = 0
                else:
                    consecutive_no_new += 1
                    print(f"  [{site_num}] Click #{click_count}: No new links ({consecutive_no_new}/{limit_click_count})")

            return len(self.collected_links) - initial_count
        except Exception as e:
            print(f"  [{site_num}] Error occurred: {str(e)}")
            return 0
        finally:
            await page.close()

    async def _collect_paginated_site_parallel(self, browser_context, base_url: str, site_num: int) -> int:
        """Handle classic pagination by visiting /page/N endpoints in parallel batches."""
        initial_count = len(self.collected_links)
        current_domain = urlparse(base_url).netloc.lower()

        # Page 1
        first_page_links = await self._extract_links_from_single_page(browser_context, base_url, 1, site_num)
        if not first_page_links:
            print(f"  [{site_num}] No links found on the first page.")
            return 0

        self.collected_links.update(first_page_links)
        print(f"  [{site_num}] Page 1: +{len(first_page_links)} links")

        page_num = 2
        max_pages = 100
        batch_size = self.max_concurrent_pages

        while page_num <= max_pages:
            batch_pages = list(range(page_num, min(page_num + batch_size, max_pages + 1)))
            semaphore = asyncio.Semaphore(self.max_concurrent_pages)
            tasks = []
            for page in batch_pages:
                # Normalize base to avoid double slashes and keep trailing slash for WP
                base = base_url.rstrip("/")
                url = f"{base}/page/{page}/"
                if "checkpoint.com" in current_domain:
                    url = f"{base}/page/{page}/"
                task = self._extract_links_from_single_page_with_semaphore(
                    semaphore, browser_context, url, page, site_num
                )
                tasks.append(task)

            results = await asyncio.gather(*tasks)

            any_new_links = False
            for idx, page_links in enumerate(results):
                if page_links:
                    new_links = page_links - self.collected_links
                    if new_links:
                        self.collected_links.update(new_links)
                        actual_page = batch_pages[idx]
                        print(f"  [{site_num}] Page {actual_page}: +{len(new_links)} links")
                        any_new_links = True

            if not any_new_links:
                print(f"  [{site_num}] Batch pages {batch_pages[0]}-{batch_pages[-1]}: No new links — stopping")
                break

            page_num += batch_size

        return len(self.collected_links) - initial_count

    async def _extract_links_from_single_page_with_semaphore(self, semaphore, browser_context, url, page_num, site_num):
        """Wrapper to apply a per-batch semaphore for page fetches."""
        async with semaphore:
            return await self._extract_links_from_single_page(browser_context, url, page_num, site_num)

    async def _goto_with_retry(self, page, url: str, listing_selectors: List[str]) -> bool:
        """Navigate with fallbacks: networkidle -> domcontentloaded -> load + selector wait."""
        # try 1: networkidle
        try:
            resp = await page.goto(url, wait_until="networkidle", timeout=20000)
            if resp and resp.status >= 400:
                return False
            for sel in listing_selectors:
                try:
                    await page.wait_for_selector(sel, timeout=2000)
                    return True
                except Exception:
                    continue
            return True
        except Exception:
            pass

        # try 2: domcontentloaded
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            if resp and resp.status >= 400:
                return False
            for sel in listing_selectors:
                try:
                    await page.wait_for_selector(sel, timeout=3000)
                    return True
                except Exception:
                    continue
            await page.wait_for_timeout(800)
            return True
        except Exception:
            pass

        # try 3: commit + load + tiny wait
        try:
            await page.goto(url, wait_until="commit", timeout=15000)
            await page.wait_for_load_state("load")
            for sel in listing_selectors:
                try:
                    await page.wait_for_selector(sel, timeout=2000)
                    return True
                except Exception:
                    continue
            await page.wait_for_timeout(1000)
            return True
        except Exception:
            return False

    async def _extract_links_from_single_page(self, browser_context, url: str, page_num: int, site_num: int) -> set:
        """Open one page, wait for content, extract candidate links, and filter them."""
        page = await browser_context.new_page()
        try:
            domain = urlparse(url).netloc.lower()
            if "asec.ahnlab.com" in domain:
                listing_selectors = ["article", ".entry-title a", "h2 a"]
            elif "genians.co.kr" in domain:
                listing_selectors = ["h2.entry-title a", ".entry-title a", "article"]
            else:
                listing_selectors = ["h2 a", "h3 a", ".entry-title a", "article"]

            ok = await self._goto_with_retry(page, url, listing_selectors)
            if not ok:
                return set()

            links = await self._extract_links(page)
            return links
        except Exception as e:
            print(f"  [{site_num}] Page {page_num} error: {str(e)}")
            return set()
        finally:
            await page.close()

    async def _extract_links(self, page) -> set:
        """Extract anchors from the current document with domain-tuned selectors, then filter.
           For some sites, also capture publish dates from the listing cards."""
        try:
            links = set()
            current_domain = urlparse(page.url).netloc.lower()

            # Domain-tuned selectors
            if "thedfirreport.com" in current_domain:
                selectors = ["h2 a", ".entry-title a"]
            elif "fortinet.com" in current_domain:
                selectors = ["h2 a", ".post-title a"]
            elif "checkpoint.com" in current_domain:
                selectors = ["a"]
            elif "genians.co.kr" in current_domain:
                # Extract links and dates from listing cards when available
                cards = await page.locator("div.post-content, article").all()
                for card in cards:
                    try:
                        a = card.locator("h2.entry-title a").first
                        if not await a.count():
                            a = card.locator(".entry-title a").first
                        href = await a.get_attribute("href") if await a.count() else None
                        if href:
                            absolute_url = urljoin(page.url, href)
                            links.add(absolute_url)
                            # Try to read date text near meta
                            meta = card.locator("time[datetime], div.post-meta, span.post-date").first
                            date_text = None
                            if await meta.count():
                                date_text = await meta.get_attribute("datetime")
                                if not date_text:
                                    date_text = (await meta.text_content() or "").strip()
                            dt = self._parse_date_string(date_text) if date_text else None
                            if dt:
                                self.link_dates[absolute_url] = dt
                    except Exception:
                        continue
                # Also fallback to generic selectors
                selectors = ["h2.entry-title a", ".entry-title a", "h2 a"]
            elif "asec.ahnlab.com" in current_domain:
                selectors = ["h2 a", "h3 a", ".entry-title a", 'a[href*="/ko/"]']
            else:
                selectors = ["h2 a", "h3 a"]

            # Gather raw links via selectors
            for selector in selectors:
                try:
                    elements = await page.locator(selector).all()
                    for element in elements:
                        try:
                            href = await element.get_attribute("href")
                            if href:
                                absolute_url = urljoin(page.url, href)
                                links.add(absolute_url)
                        except Exception:
                            continue
                except Exception:
                    continue

            # Apply generic filtering
            filtered_links = set()
            for link in links:
                if self._is_valid_universal_link(link, page.url, current_domain):
                    filtered_links.add(link)

            return filtered_links

        except Exception as e:
            print(f"Error while extracting links: {str(e)}")
            return set()

    def _is_actual_content_url(self, url_lower: str, domain: str) -> bool:
        """Heuristics to decide whether the URL looks like an article/content page."""
        if "thedfirreport.com" in domain:
            if re.search(r"/20\d{2}/\d{2}/\d{2}/.+", url_lower):
                return True
            return False

        elif "fortinet.com" in domain:
            if "/blog/threat-research/" in url_lower:
                parts = url_lower.split("/")
                if len(parts) >= 5 and parts[-1] and len(parts[-1]) > 10:
                    return True
            return False

        elif "checkpoint.com" in domain:
            if any(y in url_lower for y in ["2025", "2024", "2023", "2022", "2021"]):
                return True
            return False

        elif "genians.co.kr" in domain:
            if "/page/" in url_lower:
                return False
            if re.search(r"/blog/[^/]+/[^/#?]+/?$", url_lower):
                return True
            return False

        elif "asec.ahnlab.com" in domain:
            if re.search(r"/ko/\d+/?$", url_lower):
                return True
            return False

        return False

    def _is_valid_universal_link(self, url: str, current_url: str, current_domain: str = None) -> bool:
        """Generic link validation applicable across sites."""
        if not url or len(url) < 10:
            return False

        url_lower = url.lower()

        social_media = [
            "facebook.com",
            "twitter.com",
            "x.com",
            "linkedin.com",
            "instagram.com",
            "youtube.com",
            "github.com",
        ]

        file_extensions = [".jpg", ".jpeg", ".png", ".gif", ".pdf", ".zip"]

        if not current_domain:
            current_domain = urlparse(current_url).netloc.lower()

        if "checkpoint.com" in current_domain:
            exclude_patterns = ["mailto:", "javascript:", "#", "facebook.com", "twitter.com", "linkedin.com"]
        else:
            exclude_patterns = [
                "mailto:",
                "javascript:",
                "#",
                "/search",
                "/login",
                "/contact",
                "/page/",
                "?page=",
                "?p=",
                "/tag/",
                "/tags/",
                "/category/",
                "/categories/",
                "/services/",
                "/service/",
                "/products/",
                "/product/",
                "/solutions/",
                "/solution/",
                "/about/",
                "/about-us/",
                "/analysts/",
                "/testimonials/",
                "/detection-rules/",
                "/threat-intelligence/",
                "/dfir-labs/",
                "/case-artifacts/",
                "/archive/",
                "/archives/",
                "/transform",
            ]

        if any(social in url_lower for social in social_media):
            return False

        if any(ext in url_lower for ext in file_extensions):
            return False

        for pattern in exclude_patterns:
            if pattern in url_lower:
                return False

        if re.search(r"^https?://[^/]+/?$", url_lower):
            return False

        if not self._is_actual_content_url(url_lower, current_domain):
            return False

        try:
            link_domain = urlparse(url).netloc.lower()
            if (
                current_domain == link_domain
                or current_domain in link_domain
                or link_domain in current_domain
            ):
                return True
            return False
        except Exception:
            return False

    def _save_urls_by_section(self):
        """Save collected URLs grouped by base_url, sorted by publish date desc."""
        try:
            existing_content = ""
            if Path(self.output_file).exists():
                with open(self.output_file, "r", encoding="utf-8") as f:
                    existing_content = f.read()

            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            with open(self.output_file, "w", encoding="utf-8") as f:
                f.write("# CTI Links Collection\n")
                f.write(f"# Last Updated: {current_time}\n")
                f.write(f"# Total Links: {len(self.collected_links)}\n\n")

            base_urls = list(self.links_by_source.keys())
            for base_url in reversed(base_urls):
                links = self.links_by_source[base_url]
                if links:
                    # sort by date desc; unknown dates go last
                    def sort_key(u: str):
                        return self.link_dates.get(u, datetime(1970, 1, 1))

                    sorted_links = sorted(links, key=sort_key, reverse=True)

                    with open(self.output_file, "a", encoding="utf-8") as f:
                        f.write(f"## {base_url}\n")
                        f.write(f"# Collected: {len(sorted_links)} links\n")
                        f.write(f"# Date: {current_time}\n\n")
                        for link in sorted_links:
                            f.write(f"{link}\n")
                        f.write("\n")

            if existing_content.strip() and not existing_content.startswith("# CTI Links Collection"):
                with open(self.output_file, "a", encoding="utf-8") as f:
                    f.write("# =================== Previous Collections ===================\n\n")
                    f.write(existing_content)

        except Exception as e:
            print(f"Error while saving file: {str(e)}")

    def _summarize_links(self):
        """Print a summary of collected links by domain and base_url."""
        domain_count = defaultdict(int)

        print("\n=== This run summary (by base_url) ===")
        for base_url, links in self.links_by_source.items():
            if links:
                print(f"{base_url}: {len(links)} links")
                for link in links:
                    domain = urlparse(link).netloc
                    domain_count[domain] += 1

        print("\n=== Overall collected links summary (by domain) ===")
        for domain, count in sorted(domain_count.items()):
            print(f"  {domain}: {count} links")


async def main():
    collector = LinkCollector()
    await collector.collect_links()


if __name__ == "__main__":
    asyncio.run(main())
