#pragma once

#include <string>
#include <tuple>
#include <vector>

std::tuple<bool, std::string> export_step(int shape_id, const std::string& filepath);
std::tuple<bool, std::string> export_step_multi(const std::vector<int>& shape_ids, const std::string& filepath);
std::vector<int> import_step(const std::string& filepath);
