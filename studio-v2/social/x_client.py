import os
import time
from pathlib import Path

import requests
from requests_oauthlib import OAuth1

from social.common import weighted_length


class XError(RuntimeError):
    def __init__(self, message, *, status=None, uncertain=False, retry_after=600):
        super().__init__(message)
        self.status = status
        self.uncertain = uncertain
        self.retry_after = retry_after


class XClient:
    def __init__(self, api_key, api_secret, access_token, access_secret, *, base_url="https://api.x.com"):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = OAuth1(api_key, api_secret, access_token, access_secret)
        self.user_id = None

    @classmethod
    def from_env(cls):
        values = [os.environ.get(name, "").strip() for name in (
            "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET",
        )]
        return cls(*values) if all(values) else None

    def _request(self, method, path, *, transmission=False, **kwargs):
        try:
            response = self.session.request(
                method, self.base_url + path, timeout=(10, 45),
                allow_redirects=False, **kwargs,
            )
        except requests.RequestException:
            raise XError("Connexion X interrompue.", uncertain=transmission) from None
        if not 200 <= response.status_code < 300:
            retry_after = response.headers.get("Retry-After", "600")
            raise XError(
                f"X API HTTP {response.status_code}.",
                status=response.status_code,
                uncertain=transmission and response.status_code >= 500,
                retry_after=int(retry_after) if retry_after.isdigit() else 600,
            )
        if not response.content and not transmission:
            return {}
        try:
            payload = response.json()
        except ValueError:
            raise XError("Réponse X illisible.", uncertain=transmission) from None
        if payload.get("errors") and not payload.get("data"):
            raise XError("X n'a pas confirmé l'opération.", uncertain=transmission)
        return payload

    def verify_account(self, username):
        data = self._request("GET", "/2/users/me")["data"]
        if data.get("username", "").casefold() != username.lstrip("@").casefold():
            raise XError("Les identifiants X ne correspondent pas au compte configuré.")
        self.user_id = str(data["id"])
        return self.user_id

    def create_post(self, text, *, made_with_ai=True, media_id=None):
        if not text.strip() or weighted_length(text) > 280:
            raise XError("Post vide ou trop long pour X.")
        body = {"text": text, "made_with_ai": made_with_ai}
        if media_id:
            body["media"] = {"media_ids": [str(media_id)]}
        data = self._request("POST", "/2/tweets", json=body, transmission=True)
        post_id = data.get("data", {}).get("id")
        if not post_id:
            raise XError("X n'a pas renvoyé l'identifiant du post.", uncertain=True)
        return str(post_id)

    def upload_video(self, path):
        path = Path(path)
        payload = self._request("POST", "/2/media/upload/initialize", json={
            "total_bytes": path.stat().st_size,
            "media_type": "video/mp4", "media_category": "tweet_video",
        })
        media_id = str(payload["data"]["id"])
        with path.open("rb") as video:
            segment = 0
            for chunk in iter(lambda: video.read(4 * 1024 * 1024), b""):
                self._request(
                    "POST", f"/2/media/upload/{media_id}/append",
                    data={"segment_index": str(segment)},
                    files={"media": ("chunk.mp4", chunk, "application/octet-stream")},
                )
                segment += 1
        payload = self._request("POST", f"/2/media/upload/{media_id}/finalize")
        info = payload.get("data", {}).get("processing_info")
        deadline = time.monotonic() + 180
        while info and info.get("state") != "succeeded":
            if info.get("state") == "failed" or time.monotonic() >= deadline:
                raise XError("La préparation de la vidéo X a échoué.")
            time.sleep(min(10, max(1, info.get("check_after_secs", 1))))
            payload = self._request("GET", "/2/media/upload", params={
                "command": "STATUS", "media_id": media_id,
            })
            info = payload.get("data", {}).get("processing_info")
        return media_id

    def send_dm(self, user_id, text):
        if not str(user_id).isdigit():
            raise XError("Identifiant destinataire invalide.")
        data = self._request(
            "POST", f"/2/dm_conversations/with/{user_id}/messages",
            json={"text": text}, transmission=True,
        )
        event_id = data.get("data", {}).get("dm_event_id")
        if not event_id:
            raise XError("X n'a pas confirmé le DM.", uncertain=True)
        return str(event_id)

    def mentions(self, since_id=None):
        params = {"max_results": 100, "tweet.fields": "author_id,created_at,referenced_tweets,conversation_id"}
        if since_id:
            params["since_id"] = since_id
        while True:
            payload = self._request("GET", f"/2/users/{self.user_id}/mentions", params=params)
            yield from payload.get("data", [])
            next_token = payload.get("meta", {}).get("next_token")
            if not next_token:
                break
            params["pagination_token"] = next_token

    def dm_events(self):
        payload = self._request("GET", "/2/dm_events", params={
            "max_results": 100, "dm_event.fields": "sender_id,text,created_at",
        })
        return payload.get("data", [])
