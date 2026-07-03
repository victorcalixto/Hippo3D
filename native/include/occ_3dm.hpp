#pragma once

#include <string>
#include <tuple>
#include <vector>

std::tuple<bool, std::string> export_3dm(int shape_id, const std::string& filepath);
std::tuple<bool, std::string> export_3dm_brep(int shape_id, const std::string& filepath);
std::tuple<bool, std::string> export_3dm_multi(const std::vector<int>& shape_ids, const std::string& filepath);

// Multi-object STEP export
std::tuple<bool, std::string> export_step_multi(const std::vector<int>& shape_ids, const std::string& filepath);

std::vector<std::pair<int, std::string>> import_3dm(const std::string& filepath);
