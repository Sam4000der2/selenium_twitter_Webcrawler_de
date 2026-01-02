import argparse
import asyncio
import io
import os
from pathlib import Path

from PIL import Image

import mastodon_bot


class DummyModels:
    def __init__(self, text: str = "Beispiel-Alt-Text aus Dummy-Modell."):
        self.text = text

    def generate_content(self, model=None, config=None, contents=None):
        return type("Resp", (), {"text": self.text})()


class DummyClient:
    def __init__(self, text: str = "Beispiel-Alt-Text aus Dummy-Modell."):
        self.models = DummyModels(text)


class DummyManager:
    def __init__(self):
        self.calls = []

    def get_candidate_models(self):
        return ["dummy-model"]

    def mark_success(self, name, error=""):
        self.calls.append(("success", name, error))

    def mark_failed(self, name, error=""):
        self.calls.append(("failed", name, error))

    def mark_not_found(self, name, error=""):
        self.calls.append(("not_found", name, error))

    def mark_quota(self, name, error=""):
        self.calls.append(("quota", name, error))


def load_and_prepare_image(path: Path | None) -> bytes:
    """
    Lädt ein Bild von Disk (oder erzeugt Dummy) und führt dieselbe
    Konvertierung wie der Mastodon-Bot durch, damit die Bytes identisch
    verarbeitet werden.
    """
    if path is None:
        img = Image.new("RGB", (64, 64), color=(255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()
        ext = ".png"
    else:
        with path.open("rb") as f:
            raw = f.read()
        ext = path.suffix.lower()

    return mastodon_bot.prepare_image_for_upload(raw, ext)


async def run_test(image_path: Path | None, use_dummy: bool):
    image_bytes = load_and_prepare_image(image_path)

    if use_dummy:
        client = DummyClient()
        manager = DummyManager()
        original_manager = mastodon_bot.gemini_manager
        mastodon_bot.gemini_manager = manager
    else:
        client = mastodon_bot.client
        manager = mastodon_bot.gemini_manager
        original_manager = None

    try:
        result = await mastodon_bot.generate_alt_text(
            client=client,
            image_bytes=image_bytes,
            original_tweet_full="Dies ist ein Test-Tweet für die Alt-Text-Generierung.",
            twitter_account="TestAccount",
            tweet_url="https://example.com/tweet/123"
        )
        print("IMAGE_USED:", image_path if image_path else "Dummy 64x64 rot")
        print("GENERATED_ALT_TEXT:", result)
        print("MODEL_STATUS_UPDATES:", getattr(manager, "calls", "real gemini_manager"))
    finally:
        if original_manager is not None:
            mastodon_bot.gemini_manager = original_manager


def parse_args():
    parser = argparse.ArgumentParser(description="Alt-Text-Generator testen (ohne Mastodon-Post).")
    parser.add_argument("--image", type=str, help="Pfad zu einem Bild", required=False)
    parser.add_argument(
        "--dummy",
        action="store_true",
        help="Dummy-Gemini (offline) nutzen. Ohne Flag wird der echte Gemini-Client/-Manager genutzt."
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not args.dummy and not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY fehlt. Setze die Variable oder nutze --dummy.")

    img_path = Path(args.image).resolve() if args.image else None
    asyncio.run(run_test(img_path, args.dummy))
