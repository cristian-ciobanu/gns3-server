#
# Copyright (C) 2026 GNS3 Technologies Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
API routes for API key management.
"""

import secrets
import bcrypt
from uuid import uuid4, UUID

from fastapi import APIRouter, Depends, status

from gns3server import schemas
from gns3server.schemas.controller.tokens import TokenData
from gns3server.db.repositories.api_keys import ApiKeysRepository
from gns3server.db.repositories.users import UsersRepository
from .dependencies.database import get_repository
from .dependencies.authentication import get_current_active_user

import logging

log = logging.getLogger(__name__)

router = APIRouter(prefix="/access/api-keys", tags=["API Keys"])

API_KEY_PREFIX = "gns3_"
API_KEY_BYTES = 32  # 256-bit key, results in 64 hex chars


def _generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.

    Returns:
        Tuple of (full_key, key_hash, key_prefix)
    """
    random_bytes = secrets.token_hex(API_KEY_BYTES)
    raw_key = API_KEY_PREFIX + random_bytes
    key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode()
    key_prefix = raw_key[: len(API_KEY_PREFIX) + 8]  # gns3_ + first 8 hex chars
    return raw_key, key_hash, key_prefix


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    api_key_data: schemas.ApiKeyCreate,
    current_user: schemas.User = Depends(get_current_active_user),
    api_keys_repo: ApiKeysRepository = Depends(get_repository(ApiKeysRepository)),
) -> dict:
    """Create a new API key. The full key is returned only once."""

    raw_key, key_hash, key_prefix = _generate_api_key()
    db_key = await api_keys_repo.create_api_key(
        api_key_id=uuid4(),
        user_id=current_user.user_id,
        name=api_key_data.name,
        key_hash=key_hash,
        key_prefix=key_prefix,
    )
    return {
        "api_key_id": str(db_key.api_key_id),
        "api_key": raw_key,
        "name": db_key.name,
        "key_prefix": db_key.key_prefix,
        "created_at": db_key.created_at.isoformat() if db_key.created_at else None,
    }


@router.get("")
async def list_api_keys(
    current_user: schemas.User = Depends(get_current_active_user),
    api_keys_repo: ApiKeysRepository = Depends(get_repository(ApiKeysRepository)),
) -> list[dict]:
    """List all API keys for the current user."""

    keys = await api_keys_repo.get_api_keys_by_user(current_user.user_id)
    return [
        {
            "api_key_id": str(k.api_key_id),
            "name": k.name,
            "key_prefix": k.key_prefix,
            "created_at": k.created_at.isoformat() if k.created_at else None,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "revoked": k.revoked,
        }
        for k in keys
    ]


@router.delete(
    "/{api_key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_api_key(
    api_key_id: UUID,
    current_user: schemas.User = Depends(get_current_active_user),
    api_keys_repo: ApiKeysRepository = Depends(get_repository(ApiKeysRepository)),
) -> None:
    """Revoke an API key (soft delete — sets revoked=True)."""

    key = await api_keys_repo.get_api_key(api_key_id)
    if not key:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="API key not found")

    # Only the key owner can revoke it
    if key.user_id != current_user.user_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Cannot revoke another user's API key")

    await api_keys_repo.revoke_api_key(api_key_id)
