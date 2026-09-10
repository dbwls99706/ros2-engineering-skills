# Expected: Launch File Review

## Required Issues Detected

### Errors
1. **Missing `generate_launch_description`**: Function is named `create_nodes` instead
   of `generate_launch_description` — launch system will not find it
2. **Obsolete constructor arguments**: Modern `Node` requires `executable`;
   `node_executable`, `node_name`, and `node_namespace` must be migrated.
   Older API versions may accept these aliases with warnings; do not assume that
   behavior without checking the target distribution.

### Warnings
1. **Hardcoded path**: `/home/user/catkin_ws/config/camera.yaml` is not portable —
   use `os.path.join(get_package_share_directory('my_robot'), 'config', 'camera.yaml')`
   or `PathJoinSubstitution` with `FindPackageShare`. Install the config file in
   the package share directory; its existence and contents are not supplied.
2. **Misleading basename**: Naming a LiDAR `camera` is confusing. Renaming it is
   a clarity improvement, not a required fix for a proven graph collision.

### Info
- Second node uses correct modern keywords (`executable`, `name`)
- Fully qualified names include namespaces. After keyword migration in a
  standalone launch, `/sensors/camera` and `/camera` are distinct even though
  their basenames match. An omitted namespace inherits the launch context.
- Static review cannot establish installed packages, executable availability,
  YAML parameter selectors, or successful runtime parameter loading.

## Required Corrected Version
Must include:
- Function renamed to `generate_launch_description`
- All deprecated keywords replaced with modern equivalents
- Hardcoded path replaced with an installed package share lookup
- Preserve intended namespaces; optionally rename the LiDAR to `lidar`
- Proper imports for the chosen package share lookup and path construction
