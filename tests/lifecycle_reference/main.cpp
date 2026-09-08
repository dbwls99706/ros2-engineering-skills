// Run the verbatim documented C++ node against positive and negative data controls.
#include <array>
#include <chrono>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include "reference_node.hpp"

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  std::array<int, 3> control{}, filtered{};
  bool valid = true;
  for (int scenario = 0; scenario != 2; ++scenario) {
    // The never-activated control needs its own inputs. Otherwise an input
    // queued while inactive may legitimately be processed after activation.
    rclcpp::NodeOptions options;
    options.arguments({"--ros-args", "-r", "raw_scan:=raw_scan_" + std::to_string(scenario),
      "-r", "filtered_scan:=filtered_scan_" + std::to_string(scenario)});
    auto processor = std::make_shared<my_robot_perception::LidarProcessor>(options);
    auto observer = std::make_shared<rclcpp::Node>("reference_observer", options);
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(processor->get_node_base_interface());
    executor.add_node(observer);
    auto sender = observer->create_publisher<sensor_msgs::msg::LaserScan>(
      "raw_scan", rclcpp::SensorDataQoS());
    auto positive = observer->create_subscription<sensor_msgs::msg::LaserScan>(
      "raw_scan", rclcpp::SensorDataQoS(),
      [&](sensor_msgs::msg::LaserScan::ConstSharedPtr message) {
        control.at(std::stoi(message->header.frame_id))++;
      });
    auto receiver = observer->create_subscription<sensor_msgs::msg::LaserScan>(
      "filtered_scan", rclcpp::SensorDataQoS(),
      [&](sensor_msgs::msg::LaserScan::ConstSharedPtr message) {
        // Classify by the input phase, not callback arrival time: an active-phase
        // sample may still be queued when the node has already deactivated.
        filtered.at(std::stoi(message->header.frame_id))++;
        valid = valid && message->ranges.size() == 3 && std::isnan(message->ranges[0]) &&
          message->ranges[1] == 1.0f && std::isnan(message->ranges[2]);
      });
    auto send_phase = [&](int phase) {
        const auto begin = std::chrono::steady_clock::now();
        const auto deadline = begin + std::chrono::seconds(3);
        while (std::chrono::steady_clock::now() < deadline) {
          sensor_msgs::msg::LaserScan message;
          message.header.frame_id = std::to_string(phase);
          message.ranges = {-2.0f, 1.0f, 99.0f};
          sender->publish(message);
          executor.spin_some();
          if (std::chrono::steady_clock::now() - begin >= std::chrono::milliseconds(700) &&
            control.at(phase) >= 25 && (phase != 1 || filtered.at(phase) >= 25))
          {
            break;
          }
          std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
      };
    if (processor->configure().id() != lifecycle_msgs::msg::State::PRIMARY_STATE_INACTIVE) {
      throw std::runtime_error("Reference node did not become inactive");
    }
    if (scenario == 0) {
      send_phase(0);
    } else {
      if (processor->activate().id() != lifecycle_msgs::msg::State::PRIMARY_STATE_ACTIVE) {
        throw std::runtime_error("Reference node did not become active");
      }
      send_phase(1);
      if (processor->deactivate().id() != lifecycle_msgs::msg::State::PRIMARY_STATE_INACTIVE) {
        throw std::runtime_error("Reference node did not deactivate");
      }
      send_phase(2);
    }
    if (processor->cleanup().id() != lifecycle_msgs::msg::State::PRIMARY_STATE_UNCONFIGURED) {
      throw std::runtime_error("Reference node did not clean up");
    }
  }
  std::cout << "{\"control_inactive\":" << control[0] <<
    ",\"inactive_received\":" << filtered[0] <<
    ",\"control_active\":" << control[1] <<
    ",\"active_received\":" << filtered[1] <<
    ",\"control_deactivated\":" << control[2] <<
    ",\"deactivated_received\":" << filtered[2] <<
    ",\"valid_filter_output\":" << (valid ? "true" : "false") << "}" << std::endl;
  const bool passed = control[0] >= 25 && control[1] >= 25 && control[2] >= 25 &&
    filtered[0] == 0 && filtered[1] >= 25 && filtered[2] == 0 && valid;
  rclcpp::shutdown();
  return passed ? 0 : 1;
}
