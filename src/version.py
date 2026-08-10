"""Application and release-channel identity."""

APP_VERSION = "1.4.0"
GITHUB_REPOSITORY = "yuanxinxu190645/Lazybones"
GITHUB_RELEASES_URL = (
    "https://github.com/" + GITHUB_REPOSITORY + "/releases"
)
GITHUB_LATEST_RELEASE_API = (
    "https://api.github.com/repos/" + GITHUB_REPOSITORY +
    "/releases/latest"
)


def portable_asset_name(version: str) -> str:
    clean = str(version).strip().lstrip("vV")
    return "Lazybones-v" + clean + "-portable-win64.zip"
