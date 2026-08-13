"""Minimal Docker Registry HTTP API v2 client for update detection.

Only handles anonymous/public access, which covers Docker Hub images
(e.g. official ones like Caddy) and public GitHub Container Registry (GHCR)
packages, which is what images built by a plain GitHub Actions workflow end
up as. Anything requiring authentication, or any reference this module
can't confidently parse, is treated as "can't auto-detect" rather than
guessed at.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import aiohttp

_LOGGER = logging.getLogger(__name__)

DOCKER_HUB_REGISTRY = "docker.io"
DOCKER_HUB_API_HOST = "registry-1.docker.io"
DOCKER_HUB_AUTH_REALM = "https://auth.docker.io/token"
DOCKER_HUB_AUTH_SERVICE = "registry.docker.io"

MANIFEST_ACCEPT_HEADERS = ", ".join(
    [
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
    ]
)

_WWW_AUTHENTICATE_RE = re.compile(
    r'Bearer\s+realm="(?P<realm>[^"]+)"(?:,\s*service="(?P<service>[^"]+)")?'
)


class RegistryError(Exception):
    """Raised when a registry cannot be queried (auth required, network error, ...)."""


@dataclass
class ImageReference:
    """A parsed 'registry/repo:tag' style Docker image reference."""

    registry: str
    repository: str
    tag: str | None
    digest: str | None

    @property
    def is_pinned_by_digest(self) -> bool:
        """Return True if the reference is already pinned to an exact digest."""
        return self.digest is not None


def parse_image_reference(ref: str) -> ImageReference:
    """Parse a Docker image reference into registry/repository/tag parts."""
    remainder = ref
    digest: str | None = None
    if "@" in remainder:
        remainder, digest = remainder.split("@", 1)

    tag: str | None = None
    last_slash = remainder.rfind("/")
    last_colon = remainder.rfind(":")
    if last_colon > last_slash:
        remainder, tag = remainder[:last_colon], remainder[last_colon + 1 :]

    parts = remainder.split("/")
    first = parts[0]
    if len(parts) > 1 and ("." in first or ":" in first or first == "localhost"):
        registry = first
        repository = "/".join(parts[1:])
    else:
        registry = DOCKER_HUB_REGISTRY
        repository = remainder
        if "/" not in repository:
            repository = f"library/{repository}"

    if digest is None and tag is None:
        tag = "latest"

    return ImageReference(registry=registry, repository=repository, tag=tag, digest=digest)


async def _async_get_bearer_token(
    session: aiohttp.ClientSession, realm: str, service: str | None, repository: str, timeout: int
) -> str:
    params = {"scope": f"repository:{repository}:pull"}
    if service:
        params["service"] = service
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with session.get(realm, params=params, timeout=client_timeout) as resp:
        if resp.status != 200:
            raise RegistryError(f"Token-Anfrage an {realm} fehlgeschlagen ({resp.status})")
        data = await resp.json()
    token = data.get("token") or data.get("access_token")
    if not token:
        raise RegistryError(f"Keine Zugriffs-Token in Antwort von {realm}")
    return token


async def async_get_remote_digest(
    session: aiohttp.ClientSession, image: ImageReference, timeout: int = 15
) -> tuple[str, str, dict]:
    """Look up the manifest digest for an image's tag on its registry.

    Raises RegistryError if the registry can't be queried anonymously or
    the reference can't be resolved (private image, unknown host, ...).
    """
    if image.is_pinned_by_digest:
        raise RegistryError("Image ist bereits über einen Digest fixiert")

    if image.registry == DOCKER_HUB_REGISTRY:
        api_host = DOCKER_HUB_API_HOST
        realm, service = DOCKER_HUB_AUTH_REALM, DOCKER_HUB_AUTH_SERVICE
    else:
        api_host = image.registry
        realm, service = await _async_discover_auth(session, api_host, timeout)

    token = None
    if realm:
        token = await _async_get_bearer_token(session, realm, service, image.repository, timeout)

    headers = {"Accept": MANIFEST_ACCEPT_HEADERS}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    manifest_url = f"https://{api_host}/v2/{image.repository}/manifests/{image.tag}"
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with session.get(manifest_url, headers=headers, timeout=client_timeout) as resp:
        if resp.status != 200:
            raise RegistryError(
                f"Manifest-Abfrage für {image.repository}:{image.tag} fehlgeschlagen "
                f"({resp.status})"
            )
        digest = resp.headers.get("Docker-Content-Digest")
        content_type = resp.headers.get("Content-Type", "")
        body = await resp.json()

    if digest is None:
        raise RegistryError("Registry hat keinen Docker-Content-Digest-Header geliefert")

    return digest, content_type, body


async def _async_discover_auth(
    session: aiohttp.ClientSession, api_host: str, timeout: int
) -> tuple[str | None, str | None]:
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with session.get(f"https://{api_host}/v2/", timeout=client_timeout) as resp:
        if resp.status == 200:
            return None, None
        if resp.status != 401:
            raise RegistryError(f"Registry {api_host} nicht erreichbar ({resp.status})")
        challenge = resp.headers.get("WWW-Authenticate", "")

    match = _WWW_AUTHENTICATE_RE.search(challenge)
    if not match:
        raise RegistryError(f"Konnte Auth-Anforderung von {api_host} nicht auswerten")
    return match.group("realm"), match.group("service")


async def async_check_for_update(
    session: aiohttp.ClientSession, image_ref: str, local_digest: str | None, timeout: int = 15
) -> tuple[bool, str] | None:
    """Check whether a newer image is available for the given reference.

    Returns (update_available, remote_digest), or None if this can't be
    determined automatically (private/unreachable registry, digest-pinned
    reference, ...).
    """
    image = parse_image_reference(image_ref)
    if image.is_pinned_by_digest:
        return None

    try:
        remote_digest, content_type, body = await async_get_remote_digest(session, image, timeout)
    except (RegistryError, aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.debug("Update-Prüfung für %s nicht möglich: %s", image_ref, err)
        return None

    if local_digest is None:
        # No recorded provenance for the locally running image (e.g. built
        # directly on the server rather than pulled from a registry) - we
        # cannot tell whether it matches what's remote, so this isn't a
        # "definitely outdated" call, it's a "can't tell" one.
        return None

    if local_digest == remote_digest:
        return False, remote_digest

    # Multi-arch images: the digest docker stores locally is often the
    # platform-specific manifest digest, not the top-level manifest list
    # digest we just compared against. If the remote is a list/index,
    # check whether the local digest is one of its children before
    # concluding there is an update.
    if "manifest.list" in content_type or "image.index" in content_type:
        child_digests = {entry.get("digest") for entry in body.get("manifests", [])}
        if local_digest in child_digests:
            return False, remote_digest

    return True, remote_digest
