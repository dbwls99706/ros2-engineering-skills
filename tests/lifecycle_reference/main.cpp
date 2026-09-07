// Run the verbatim documented C++ node against positive and negative data controls.
#include <array>
#include <chrono>
#include <cmath>
#include <iostream>
#include <thread>
#include "reference_node.hpp"

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  int result = 0;
  {
    auto processor = std::make_shared<my_robot_perception::LidarProcessor>();
    auto observer = std::make_shared<rclcpp::Node>("reference_observer");
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(processor->get_node_base_interface());
    executor.add_node(observer);
    std::array<int, 3> control{}, filtered{};
    bool valid = true;
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
    processor->configure();
    send_phase(0);
    processor->activate();
    send_phase(1);
    processor->deactivate();
    send_phase(2);
    processor->cleanup();
    std::cout << "{\"control_inactive\":" << control[0] <<
      ",\"inactive_received\":" << filtered[0] <<
      ",\"control_active\":" << control[1] <<
      ",\"active_received\":" << filtered[1] <<
      ",\"control_deactivated\":" << control[2] <<
      ",\"deactivated_received\":" << filtered[2] <<
      ",\"valid_filter_output\":" << (valid ? "true" : "false") << "}" << std::endl;
    if (control[0] < 25 || control[1] < 25 || control[2] < 25 ||
      filtered[0] != 0 || filtered[1] < 25 || filtered[2] != 0 || !valid)
    {
      result = 1;
    }
  }
  rclcpp::shutdown();
  return result;
}
