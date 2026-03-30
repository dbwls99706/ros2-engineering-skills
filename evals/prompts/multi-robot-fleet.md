# Multi-Robot Fleet Coordination

## Scenario

You are deploying a fleet of 5 mobile robots in a warehouse using ROS 2 Jazzy.
Each robot runs its own navigation stack (Nav2). Requirements:
1. Each robot must have isolated DDS discovery (no cross-talk between robot stacks)
2. A central fleet manager node must communicate with all robots
3. Use ROS_DOMAIN_ID or DDS discovery partitioning appropriately
4. Handle namespace conventions for multi-robot tf trees

## Question

Design the fleet communication architecture. Include DDS configuration,
namespace strategy, tf frame conventions, and the fleet manager node pattern.
