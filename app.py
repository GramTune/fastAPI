# app.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Any, Dict
from datetime import datetime, timezone
import asyncio

from playwright.async_api import async_playwright, TimeoutError

app = FastAPI(title="Surebet Scraper API")

# Mémoire simple pour stocker le dernier résultat
LAST_RESULT: Dict[str, Any] = {}

class ScrapeRequest(BaseModel):
    headless: bool = True
    login_email: Optional[str] = None
    login_password: Optional[str] = None
    login_url: Optional[str] = None
    base_url: Optional[str] = "https://fr.surebet.com/surebets"
    timeout: Optional[int] = 30  # temps d'attente pour certaines opérations (sec)

@app.get("/ping")
async def ping():
    return {"pong": True}

@app.get("/last")
async def last():
    if not LAST_RESULT:
        raise HTTPException(status_code=404, detail="No result available yet")
    return LAST_RESULT

@app.post("/scrape")
async def scrape(req: ScrapeRequest):
    """
    Lance un seul cycle de scraping et renvoie le JSON extrait.
    Attention: opération bloquante côté requête (peut durer plusieurs secondes).
    """
    result = await run_one_scrape(req)
    # Enregistrer en mémoire pour récupération ultérieure
    LAST_RESULT.clear()
    LAST_RESULT.update(result)
    return result

async def run_one_scrape(req: ScrapeRequest) -> Dict[str, Any]:
    """Lance Playwright, (optionnel) tente login, navigue puis extrait les surebets."""
    start_ts = datetime.now(timezone.utc).isoformat()
    async with async_playwright() as p:
        browser = None
        try:
            # args utiles pour environnements cloud (Render, Heroku...)
            launch_args = {
                "headless": req.headless,
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--disable-gpu",
                ]
            }
            browser = await p.chromium.launch(**launch_args)
            context = await browser.new_context(locale="fr-FR")
            page = await context.new_page()

            # Fonction simple de login (tentative) : on cherche des inputs courants
            async def try_login():
                if not (req.login_email and req.login_password):
                    return False
                try:
                    if req.login_url:
                        await page.goto(req.login_url, wait_until="domcontentloaded", timeout=req.timeout*1000)
                    else:
                        await page.goto(req.base_url, wait_until="domcontentloaded", timeout=req.timeout*1000)
                    # selectors basiques
                    email_selectors = ['input[type="email"]','input[name="email"]','input[name="login"]','input[id*="email"]']
                    pwd_selectors = ['input[type="password"]','input[name="password"]','input[id*="password"]']
                    submitted = False
                    for sel in email_selectors:
                        try:
                            await page.wait_for_selector(sel, timeout=3000)
                            await page.fill(sel, req.login_email)
                            break
                        except TimeoutError:
                            continue
                    for sel in pwd_selectors:
                        try:
                            await page.wait_for_selector(sel, timeout=3000)
                            await page.fill(sel, req.login_password)
                            # try press Enter
                            await page.press(sel, "Enter")
                            submitted = True
                            break
                        except TimeoutError:
                            continue
                    # give a short time to process
                    await page.wait_for_timeout(2000)
                    return True if submitted else False
                except Exception:
                    return False

            # Si credentials fournis, tenter login (silencieux, heuristique)
            if req.login_email and req.login_password:
                try:
                    await try_login()
                except Exception:
                    pass

            # Navigation vers la page cible
            await page.goto(req.base_url, wait_until="domcontentloaded", timeout=req.timeout*1000)
            # scroll progressif pour charger les éléments
            for _ in range(4):
                await page.evaluate("() => window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(500)

            # JS d'extraction (issu de ton script, adapté)
            js_extract = r"""
            () => {
              function textOr(el){ return el ? el.textContent.trim() : null }
              const results = {}
              const blocks = Array.from(document.querySelectorAll("tbody.surebet_record, tr.surebet_record, .surebets-list tbody"))
              blocks.forEach((block, bi) => {
                const trs = Array.from(block.querySelectorAll("tr")).filter(tr => !tr.classList.contains("hidden-record") && (tr.querySelector("td.booker") || tr.querySelector(".booker")))
                if (trs.length === 0) return
                const first = trs[0]
                const profit = first.querySelector("td.profit-box span.profit") || first.querySelector(".profit")
                const age = first.querySelector("td.profit-box span.age") || first.querySelector(".age")
                const general = { profit: textOr(profit), age: textOr(age) }
                const bookmakers = trs.map(tr => {
                  const bookerTd = tr.querySelector("td.booker") || tr.querySelector(".booker")
                  if (!bookerTd) return null
                  const booker = bookerTd.querySelector("a") || bookerTd
                  const sport = bookerTd.querySelector("span.minor") || bookerTd.querySelector(".sport")
                  const timeEl = tr.querySelector("td.time abbr") || tr.querySelector("td.time") || tr.querySelector(".time")
                  const ev = tr.querySelector("td.event a") || tr.querySelector(".event")
                  const coeff = tr.querySelector("td.coeff abbr") || tr.querySelector("td.coeff") || tr.querySelector(".coeff")
                  const value = tr.querySelector("td.value a.value_link") || tr.querySelector("td.value") || tr.querySelector(".value")
                  const datetime = timeEl ? (timeEl.getAttribute("title") || timeEl.textContent.trim()) : null
                  const name = booker ? booker.textContent.replace(/●|○/g, "").replace(/\\([^)]*\\)/g, "").trim() : null
                  return {
                    bookmaker: name,
                    sport: sport ? sport.textContent.replace(/[●○]/g, "").trim() : null,
                    datetime,
                    event: ev ? ev.textContent.trim() : null,
                    type_pari: coeff ? coeff.textContent.trim() : null,
                    cote: value ? value.textContent.trim() : null
                  }
                }).filter(x => x && (x.bookmaker || x.event))
                if (bookmakers.length) results[`surebet_p${bi + 1}`] = { general_info: general, bookmakers }
              })
              return results
            }
            """
            extracted = await page.evaluate(js_extract)
            ts = datetime.now(timezone.utc).isoformat()
            return {"scraped_at": ts, "base_url": req.base_url, "data": extracted, "meta": {"started_at": start_ts}}
        finally:
            try:
                if browser:
                    await browser.close()
            except Exception:
                pass
