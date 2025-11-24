"""Headless Selenium helper to keep Streamlit Cloud apps awake."""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import Iterable, Optional

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver import Chrome, ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.remote.webelement import WebElement
from webdriver_manager.chrome import ChromeDriverManager

DEFAULT_TIMEOUT = float(os.getenv("STREAMLIT_WAKE_TIMEOUT", "30"))
WAKE_WAIT = float(os.getenv("STREAMLIT_WAKE_WAIT", "20"))
POST_CLICK_WAIT = float(os.getenv("STREAMLIT_WAKE_CONFIRM_WAIT", "60"))
POST_CLICK_POLL_INTERVAL = float(os.getenv("STREAMLIT_WAKE_POLL_INTERVAL", "5"))
WAKE_TEXT_CANDIDATES = (
    "Yes, get this app back up!",
    "Yes, get this app back up",
    "Wake up",
)
WAKE_TEST_IDS = (
    "wakeup-button-owner",
    "wakeup-button-viewer",
)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def get_streamlit_url() -> str:
    url = os.getenv("STREAMLIT_APP_URL")
    if not url:
        raise ValueError("STREAMLIT_APP_URL is required for keep-awake job")
    return url


def build_driver() -> Chrome:
    options = ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1600,900")
    options.add_argument("--disable-features=BlockThirdPartyCookies")
    options.add_argument("--disable-features=SameSiteByDefaultCookies")
    # Capture console logs to surface 4xx/5xx errors when waking
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    service = Service(ChromeDriverManager().install())
    return Chrome(service=service, options=options)


def locate_wake_button(driver: Chrome, texts: Iterable[str]) -> Optional[WebElement]:
    for test_id in WAKE_TEST_IDS:
        by_test_id = driver.find_elements(
            By.XPATH, f"//button[@data-testid='{test_id}']"
        )
        if by_test_id:
            return by_test_id[0]
    for text in texts:
        xpath = f"//button[contains(normalize-space(.), '{text}')]"
        elements = driver.find_elements(By.XPATH, xpath)
        if elements:
            return elements[0]
    return None


def poll_for_boot(driver: Chrome) -> bool:
    """Refresh until wake button disappears or timeout is reached."""
    start = time.time()
    while time.time() - start < POST_CLICK_WAIT:
        time.sleep(POST_CLICK_POLL_INTERVAL)
        driver.refresh()
        try:
            WebDriverWait(driver, DEFAULT_TIMEOUT).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except TimeoutException:
            logging.info("Timed out reloading while polling; continuing")
        if not locate_wake_button(driver, WAKE_TEXT_CANDIDATES):
            return True
    return False


def log_browser_console(driver: Chrome) -> None:
    try:
        for entry in driver.get_log("browser"):
            logging.info("browser log [%s]: %s", entry.get("level"), entry.get("message"))
    except Exception:
        # Logging is best effort; ignore if unavailable
        return


def keep_streamlit_awake(timeout: float = DEFAULT_TIMEOUT) -> bool:
    url = get_streamlit_url()
    logging.info("Pinging Streamlit app")
    driver: Optional[Chrome] = None
    try:
        driver = build_driver()
        driver.get(url)
        wait = WebDriverWait(driver, timeout)
        try:
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        except TimeoutException as exc:
            logging.error("Timed out loading page: %s", exc)
            raise

        logging.info("Waiting for wake button (up to %.0fs)", WAKE_WAIT)
        try:
            button = WebDriverWait(driver, WAKE_WAIT).until(
                lambda drv: locate_wake_button(drv, WAKE_TEXT_CANDIDATES)
            )
        except TimeoutException:
            logging.info(
                "No wake button found after waiting; app probably already awake"
            )
            return False

        button_text = button.text.strip()
        logging.info("Found wake button labeled '%s'", button_text)
        button.click()
        logging.info("Wake signal clicked successfully")
        booted = poll_for_boot(driver)
        if booted:
            logging.info("Wake button disappeared; app likely booting")
        else:
            logging.info(
                "Wake button still present after %.0fs; app may still be sleeping",
                POST_CLICK_WAIT,
            )
        return booted
    except WebDriverException as exc:
        logging.error("Selenium error: %s", exc)
        raise
    finally:
        if driver:
            log_browser_console(driver)
            driver.quit()


def main() -> int:
    configure_logging()
    try:
        woke_up = keep_streamlit_awake()
    except Exception as exc:
        logging.error("Keep-awake run failed: %s", exc)
        return 1
    status = "woke app" if woke_up else "already awake"
    logging.info("Keep-awake completed: %s", status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
