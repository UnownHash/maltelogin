from __future__ import annotations

import re
from typing import TYPE_CHECKING

import httpx
from loguru import logger
from curl_cffi import requests

from .constants import ACCESS_URL, COOKIE_STORAGE

if TYPE_CHECKING:
    from .reese_cookie import CookieMonster

logger = logger.bind(name="PTC")


class LoginException(Exception):
    """generic login exception, don't log the traceback"""

    pass


class InvalidCredentials(LoginException):
    """Invalid account credentials"""

    pass


class PtcAuth:
    def __init__(self, cookie_monster: CookieMonster):
        self.cookie_monster = cookie_monster

    async def auth(self, username: str, password: str, full_url: str) -> str:
        logger.info(f"Starting auth for {username}")

        # proxies = None
        # if proxy:
        #     proxies = {"http://": proxy, "https://": proxy}

        attempts = COOKIE_STORAGE + 1
        while attempts > 0:
            attempts -= 1
            cookie = await self.cookie_monster.get_reese_cookie()

            async with requests.AsyncSession(
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-us",
                    "Connection": "keep-alive",
                    "Accept-Encoding": "gzip, deflate, br",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36",
                },
                allow_redirects=True,
                verify=False,
                timeout=10,
                proxy=cookie.proxy,
                cookies=cookie.cookies,
            ) as client:
                logger.info("Calling OAUTH page")

                try:
                    resp = await client.get(full_url)
                except Exception as e:
                    logger.error(f"Error {str(e)} during OAUTH")
                    continue

                if resp.status_code == 403:
                    # TODO it doesn't seem to actually invalidate this cookie
                    logger.info("Cookie expired. Invalidating and trying again")
                    await self.cookie_monster.remove_cookie(cookie)
                    continue

                if resp.status_code != 200:
                    raise LoginException(f"OAUTH: {resp.status_code} but expected 200")

                csrf, challenge = self.__extract_csrf_and_challenge(resp.text)

                logger.info("Calling LOGIN page")

                try:
                    login_resp = await client.post(
                        ACCESS_URL + "login",
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        data={"_csrf": csrf, "challenge": challenge, "email": username, "password": password},
                    )
                except Exception as e:
                    logger.error(f"Error {str(e)} during LOGIN")
                    continue

                if login_resp.status_code == 403:
                    logger.info("Cookie expired. Invalidating and trying again")
                    await self.cookie_monster.remove_cookie(cookie)
                    continue

                if login_resp.status_code != 200:
                    raise LoginException(f"LOGIN: {login_resp.status_code} but expected 200")

                login_code = self.__extract_login_code(login_resp.text)

                if not login_code:
                    if "error-message" in login_resp.text:
                        self.check_error_on_login_page(login_resp.text)
                        logger.error(
                            f"Please send this to Malte on Discord (error page after login)\n{login_resp.text}"
                        )
                        raise LoginException("Login failed, probably invalid credentials")

                    logger.info("Calling CONSENT page")

                    try:
                        csrf_consent, challenge_consent = self.__extract_csrf_and_challenge(login_resp.text)
                    except LoginException:
                        logger.error(f"Could not find a CSRF token for account {username} - it's probably unactivated")
                        raise InvalidCredentials()

                    try:
                        resp_consent = await client.post(
                            ACCESS_URL + "consent",
                            data={"challenge": challenge_consent, "_csrf": csrf_consent, "allow_submit": "Allow"},
                        )
                    except Exception as e:
                        logger.error(f"Error {str(e)} during CONSENT")
                        continue

                    if resp_consent.status_code == 403:
                        logger.info("Cookie expired. Invalidating and trying again")
                        await self.cookie_monster.remove_cookie(cookie)
                        continue

                    if resp_consent.status_code != 200:
                        raise LoginException(f"Consent: {resp_consent.status_code} but expected 200")
                    login_code = self.__extract_login_code(resp_consent.text)
                    if not login_code:
                        raise LoginException("No Login Code after consent, please check account")
                return login_code

        raise LoginException("Exceeded max retries during PTC auth")

    def check_error_on_login_page(self, content: str):
        if "Your username or password is incorrect." in content:
            logger.warning("BROWSER: Incorrect credentials")
            raise InvalidCredentials("Incorrect account credentials")
        elif "your account has been disabled for" in content:
            logger.error("BROWSER: Account is temporarily disabled")
            raise InvalidCredentials("Account temporarily disabled")

    def __extract_login_code(self, html) -> str | None:
        matches = re.search(r"pokemongo://state=(.*?)(?:,code=(.*?))?(?='|$)", html)

        if matches and len(matches.groups()) == 2:
            return matches.group(2)

    def __extract_csrf_and_challenge(self, html: str) -> tuple[str, str]:
        csrf_regex = re.compile(r'name="_csrf" value="(.*?)">')
        challenge_regex = re.compile(r'name="challenge" value="(.*?)">')

        csrf_matches = csrf_regex.search(html)
        challenge_matches = challenge_regex.search(html)

        if csrf_matches and challenge_matches:
            return csrf_matches.group(1), challenge_matches.group(1)

        raise LoginException("Couldn't find CSRF or challenge in Auth response")
