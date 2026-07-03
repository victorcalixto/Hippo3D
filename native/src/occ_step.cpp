#include "occ_step.hpp"
#include "occ_registry.hpp"

#include <STEPControl_Writer.hxx>
#include <STEPControl_StepModelType.hxx>
#include <STEPControl_Reader.hxx>
#include <Interface_Static.hxx>
#include <IFSelect_ReturnStatus.hxx>
#include <TopoDS_Shape.hxx>
#include <BRepBuilderAPI_Transform.hxx>

#include <gp_Trsf.hxx>

std::tuple<bool, std::string> export_step(int shape_id, const std::string& filepath) {
    if (!has_shape(shape_id)) {
        return {false, "Shape ID not found in registry"};
    }

    try {
        TopoDS_Shape shape = get_shape(shape_id);

        STEPControl_Writer writer;
        IFSelect_ReturnStatus status = writer.Transfer(shape, STEPControl_StepModelType::STEPControl_AsIs);

        if (status != IFSelect_RetDone) {
            return {false, "STEP transfer failed"};
        }

        status = writer.Write(filepath.c_str());

        if (status != IFSelect_RetDone) {
            return {false, "STEP file write failed"};
        }

        return {true, "Exported STEP: " + filepath};
    } catch (const std::exception& e) {
        return {false, std::string("Export error: ") + e.what()};
    }
}

std::tuple<bool, std::string> export_step_multi(const std::vector<int>& shape_ids, const std::string& filepath) {
    if (shape_ids.empty()) {
        return {false, "No shapes selected for STEP export"};
    }

    try {
        STEPControl_Writer writer;
        for (int shape_id : shape_ids) {
            if (!has_shape(shape_id)) {
                continue;
            }
            TopoDS_Shape shape = get_shape(shape_id);
            IFSelect_ReturnStatus status = writer.Transfer(shape, STEPControl_StepModelType::STEPControl_AsIs);
            if (status != IFSelect_RetDone) {
                return {false, "STEP transfer failed for one of the shapes"};
            }
        }

        IFSelect_ReturnStatus status = writer.Write(filepath.c_str());
        if (status != IFSelect_RetDone) {
            return {false, "STEP file write failed"};
        }

        return {true, "Exported STEP: " + filepath};
    } catch (const std::exception& e) {
        return {false, std::string("STEP export error: ") + e.what()};
    }
}

std::vector<int> import_step(const std::string& filepath) {
    std::vector<int> shape_ids;

    try {
        STEPControl_Reader reader;
        IFSelect_ReturnStatus status = reader.ReadFile(filepath.c_str());

        if (status != IFSelect_RetDone) {
            throw std::runtime_error("STEP reader could not read file (status != IFSelect_RetDone).");
        }

        reader.TransferRoots();
        int nb_shapes = reader.NbShapes();

        if (nb_shapes == 0) {
            throw std::runtime_error("STEP file read OK but no shapes found (NbShapes == 0).");
        }

        for (int i = 1; i <= nb_shapes; ++i) {
            TopoDS_Shape shape = reader.Shape(i);
            if (!shape.IsNull()) {
                int shape_id = register_shape(shape);
                shape_ids.push_back(shape_id);
            }
        }

        if (shape_ids.empty()) {
            throw std::runtime_error("STEP file had shapes but all were null/empty.");
        }
    } catch (const std::runtime_error&) {
        throw;
    } catch (const std::exception& e) {
        throw std::runtime_error(std::string("STEP import exception: ") + e.what());
    }

    return shape_ids;
}
