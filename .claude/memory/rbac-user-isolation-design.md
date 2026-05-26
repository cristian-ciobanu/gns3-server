---
name: rbac-user-isolation-design
description: GNS3 RBAC user isolation design and implementation thought process
metadata:
  type: project
---

## RBAC User Isolation Design Summary

### Core Problem
GNS3 3.0 has a complete RBAC framework (ACE + Role + Privilege), but lacks user isolation implementation, causing users to see resources they shouldn't have access to.

### Design Conflict
Traditional RBAC's **path permission model** fundamentally conflicts with **user data isolation**:
- **Path permission model**: Controls which paths users can access (e.g., `/projects`)
- **User data isolation**: Controls which specific resources users can access (e.g., alice's project vs bob's project)

### Final Implementation Approach

#### Three-Step Permission Check Logic

```python
# Step 1: ACE check - basic access permission
# Get list of projects user has ACE for
ace_projects = [p for p in all_projects() if user_has_ace(p, "Project.Audit")]

# Step 2: Filter ace_projects by created_by - user's own projects
# Key: Project sharing is only available through resource pools
user_projects = [p for p in ace_projects if p.created_by == user.username]

# Step 3: Resource pool projects
# Projects shared through resource pools
pool_projects = [p for p in pool_projects]

final_projects = user_projects + pool_projects
```

### Key Design Decisions

#### 1. ACE vs created_by Relationship
- **ACE for basic access control**: Whether user can access the system
- **created_by for user isolation**: Which specific resources user can access
- **Step 2 filtering is critical**: Even with broad ACE, created_by filtering ensures user isolation

#### 2. Project Sharing Mechanism
- **Project sharing only through resource pools**: Cannot configure ACE directly for specific projects
- **Avoids complexity of direct ACE sharing**: Prevents permission configuration chaos

#### 3. Broad ACE Fault Tolerance
```
Even with configuration: ACE: Users group + User role + "/" + propagate: True
Result: Users still only see projects they created
Reason: Step 2 created_by filtering removes other users' projects
```

### Problems Solved

#### Problem 1: Missing User Isolation
- **Issue**: All users can see all projects
- **Solution**: Filter by created_by to implement user isolation

#### Problem 2: Broad ACE Breaking Isolation
- **Issue**: `path: "/" + propagate: true` breaks user isolation
- **Solution**: Step 2 created_by filtering ensures user isolation even with broad ACE

#### Problem 3: Permission Check Order Conflicts
- **Issue**: seen mechanism blocks subsequent checks
- **Solution**: Remove seen mechanism, use pipeline-style filtering

### Technical Details

#### API Layer Permission Check
```python
@router.get("/projects")  # Note: no has_privilege decorator
async def get_projects(current_user: schemas.User = Depends(get_current_active_user)):
    # Permission checks in business logic
```

#### Duplicate Prevention
- No seen_project_ids mechanism
- Natural duplicate prevention through pipeline filtering

#### Performance Considerations
- ACE check: O(n), where n is total projects
- created_by filtering: O(m), where m is ace_projects count
- Resource pool check: O(k), where k is pool project count

### Use Cases

#### Scenario 1: Personal Use
```
alice creates project → alice only sees alice's projects ✅
bob creates project → bob only sees bob's projects ✅
No extra configuration needed, automatic user isolation
```

#### Scenario 2: Team Collaboration
```
Admin creates resource pool → adds projects → team members can access
alice creates project → alice still sees her own projects ✅
Team sharing + user isolation coexist
```

#### Scenario 3: Broad ACE Configuration
```
ACE: Users group + "/" + propagate: True
alice still only sees alice's projects ✅
User isolation unaffected by ACE configuration
```

### Design Principles

#### 1. Separation of Concerns
- **Basic access permission**: Controlled by ACE
- **Data ownership**: Controlled by created_by
- **Team sharing**: Controlled by resource pools

#### 2. Defensive Design
- User isolation remains effective even with improper ACE configuration
- Secure by default: users only see their own resources

#### 3. Simplicity
- Avoid complex seen mechanisms and mutual exclusion logic
- Clear pipeline-style check order

### Relationship with Original ACE System

#### Preserved Components
- ✅ ACE framework (permission check mechanism)
- ✅ Role and privilege definitions
- ✅ Resource pool functionality

#### Improved Components
- ✅ Added user isolation (created_by filtering)
- ✅ Clarified project sharing mechanism (only through resource pools)
- ✅ Simplified permission check logic

#### Removed Components
- ❌ Removed `/projects` path privilege check
- ❌ Removed complex seen_project_ids mechanism
- ❌ Avoided "can see all non-pool projects" privilege leak

### Implementation Details
- **Main modification**: `gns3server/api/routes/controller/projects.py`
- **Function**: `get_projects()`
- **Branch**: `feature/simple-user-isolation`
- **Base branch**: `upstream/3.1`

### Key Commits
1. Implemented basic created_by filtering
2. Implemented three-layer permission check (with logic issues)
3. Fixed to correct three-step check logic

### Design Evolution Process

#### Phase 1: Simple created_by Filtering
```python
user_projects = [p for p in all_projects() if p.created_by == user.username]
```
**Issue**: Didn't integrate with ACE system

#### Phase 2: Three-Layer Check (Wrong Version)
```python
# Used seen_project_ids mechanism
# Issue: Broad ACE breaks user isolation
```

#### Phase 3: Three-Step Check (Correct Version)
```python
# Step 1: ACE check
# Step 2: Filter ace_projects by created_by
# Step 3: Resource pools
```
**Solution**: User isolation works even with broad ACE

### Relationship with RBAC Roadmap
This implementation addresses items mentioned in the roadmap:
- **Phase 1 (MVP)**: Basic project isolation implementation
- **Auto-ACE on create**: Still needs to be implemented separately
- **Template isolation**: Can use same design pattern

### Unsolved Issues

1. **Auto-ACE on project creation**: Part of roadmap Phase 1
2. **Template and image isolation**: Can apply same design pattern
3. **Default ACE configuration**: Need reasonable default permissions for Users group

### Design Limitations

1. **Project sharing only through resource pools**: No direct ACE configuration for sharing
2. **ACE configuration required**: Users need basic ACE to access system
3. **Performance considerations**: ACE check queries database for each project

### Future Improvement Directions

1. **Auto-create ACE**: Automatically add ACE for creator when creating projects
2. **Default ACE strategy**: Configure reasonable default permissions for Users group
3. **Performance optimization**: Cache ACE check results to reduce database queries

This design achieves effective user isolation while maintaining RBAC system integrity, and solves the problem of broad ACE configurations breaking isolation.

**Key insight**: The Step 2 created_by filtering is the critical innovation that allows ACE and user isolation to coexist properly.
