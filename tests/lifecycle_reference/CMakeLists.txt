cmake_minimum_required(VERSION 3.16)
project(lifecycle_reference LANGUAGES CXX)
find_package(rclcpp REQUIRED)
find_package(rclcpp_lifecycle REQUIRED)
find_package(rclcpp_components REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(lifecycle_msgs REQUIRED)
add_executable(reference_probe main.cpp)
target_compile_features(reference_probe PRIVATE cxx_std_17)
target_link_libraries(reference_probe PRIVATE
  rclcpp::rclcpp
  rclcpp_lifecycle::rclcpp_lifecycle
  rclcpp_components::component
  ${sensor_msgs_TARGETS}
  ${lifecycle_msgs_TARGETS})
