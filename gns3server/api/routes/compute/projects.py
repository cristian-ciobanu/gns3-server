#
# Copyright (C) 2015 GNS3 Technologies Inc.
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
API routes for projects.
"""

import os
import shutil
import urllib.parse

import logging

log = logging.getLogger()

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from typing import List
from uuid import UUID

from gns3server.compute.project_manager import ProjectManager
from gns3server.compute.project import Project
from gns3server.utils.path import is_safe_path
from gns3server import schemas


router = APIRouter()

# How many clients have subscribed to notifications
_notifications_listening = {}


def dep_project(project_id: UUID) -> Project:
    """
    Dependency to retrieve a project.
    """

    pm = ProjectManager.instance()
    project = pm.get_project(str(project_id))
    return project


@router.get("/projects", response_model=List[schemas.Project])
def get_compute_projects() -> List[schemas.Project]:
    """
    Get all projects opened on the compute.
    """

    pm = ProjectManager.instance()
    return [p.asdict() for p in pm.projects]


@router.post("/projects", status_code=status.HTTP_201_CREATED, response_model=schemas.Project)
def create_compute_project(project_data: schemas.ProjectCreate) -> schemas.Project:
    """
    Create a new project on the compute.
    """

    pm = ProjectManager.instance()
    project_data = jsonable_encoder(project_data, exclude_unset=True)
    project = pm.create_project(
        name=project_data.get("name"),
        path=project_data.get("path"),
        project_id=project_data.get("project_id"),
        variables=project_data.get("variables", None),
    )
    return project.asdict()


@router.put("/projects/{project_id}", response_model=schemas.Project)
async def update_compute_project(
        project_data: schemas.ProjectUpdate,
        project: Project = Depends(dep_project)
) -> schemas.Project:
    """
    Update project on the compute.
    """

    await project.update(variables=project_data.variables)
    return project.asdict()


@router.get("/projects/{project_id}", response_model=schemas.Project)
def get_compute_project(project: Project = Depends(dep_project)) -> schemas.Project:
    """
    Return a project from the compute.
    """

    return project.asdict()


@router.post("/projects/{project_id}/close", status_code=status.HTTP_204_NO_CONTENT)
async def close_compute_project(project: Project = Depends(dep_project)) -> None:
    """
    Close a project on the compute.
    """

    # FIXME
    if _notifications_listening.setdefault(project.id, 0) <= 1:
        await project.close()
        ProjectManager.instance().remove_project(project.id)
        try:
            del _notifications_listening[project.id]
        except KeyError:
            pass
    else:
        log.warning("Skip project closing, another client is listening for project notifications")


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_compute_project(project: Project = Depends(dep_project)) -> None:
    """
    Delete project from the compute.
    """

    await project.delete()
    ProjectManager.instance().remove_project(project.id)


@router.get("/projects/{project_id}/files", response_model=List[schemas.ProjectFile])
async def get_compute_project_files(project: Project = Depends(dep_project)) -> List[schemas.ProjectFile]:
    """
    Return files belonging to a project.
    """

    return await project.list_files()


@router.get("/projects/{project_id}/nodes/{node_type}/{node_id}/files", response_model=List[schemas.NodeFile])
async def get_compute_node_files(
    node_type: str,
    node_id: str,
    project: Project = Depends(dep_project),
    path: str = Query("", description="Subdirectory path within node directory"),
    recursive: bool = Query(False, description="Recursively list all files")
) -> List[schemas.NodeFile]:
    """
    Return files belonging to a specific node with detailed metadata.
    """

    node_path = f"project-files/{node_type}/{node_id}"
    return await project.list_node_files(node_path, subpath=path, recursive=recursive)


@router.get("/projects/{project_id}/files/{file_path:path}")
async def get_compute_project_file(file_path: str, project: Project = Depends(dep_project)) -> FileResponse:
    """
    Get a file from a project.
    """

    file_path = urllib.parse.unquote(file_path)
    path = os.path.normpath(file_path)

    # Raise error if user try to escape
    if not is_safe_path(path, project.path):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    path = os.path.join(project.path, path)
    if not os.path.exists(path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    return FileResponse(path, media_type="application/octet-stream")


@router.post("/projects/{project_id}/files/{file_path:path}", status_code=status.HTTP_204_NO_CONTENT)
async def write_compute_project_file(
        file_path: str,
        request: Request,
        project: Project = Depends(dep_project)
) -> None:

    file_path = urllib.parse.unquote(file_path)
    path = os.path.normpath(file_path)

    # Raise error if user try to escape
    if not is_safe_path(path, project.path):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Path is outside the project directory")

    path = os.path.join(project.path, path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, "wb+") as f:
            async for chunk in request.stream():
                f.write(chunk)

    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Permission denied writing to '{path}'")
    except OSError as e:
        log.error(f"Error writing file '{path}': {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.delete("/projects/{project_id}/files/{file_path:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_compute_project_file(
        file_path: str,
        project: Project = Depends(dep_project)
) -> None:

    file_path = urllib.parse.unquote(file_path)
    path = os.path.normpath(file_path)

    if not is_safe_path(path, project.path):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Path is outside the project directory")

    path = os.path.join(project.path, path)
    if not os.path.exists(path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Permission denied deleting '{path}'")
    except OSError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
