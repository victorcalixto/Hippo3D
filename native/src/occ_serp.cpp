#include "occ_step.hpp"
#include "occ_registry.hpp"

#include <BinTools.hxx>
#include <TopoDS_Shape.hxx>
#include <Standard_Version.hxx>

#include <zlib.h>

#include <fstream>
#include <sstream>
#include <stdexcept>
#include <cstring>
#include <vector>
#include <string>

// Minimal native .serp object blob writer/reader.
// Serpentine3D v2 stores each object as a base64-encoded OCCT binary BREP.
// We use the same binary BREP format produced by BinTools, wrapped in a
// tiny JSON document that Serpentine3D can also read/write.
//
// Real Serpentine3D .serp files are ZIP archives containing:
//   meta.json     - small manifest
//   document.json - the JSON document with the objects array
//   thumbnail.png - optional preview
// For compatibility we read ZIP-wrapped .serp files and also accept the
// raw JSON document produced by our own writer.

static std::vector<char> shape_to_brep_bytes(const TopoDS_Shape& shape) {
    if (shape.IsNull()) {
        throw std::runtime_error("Cannot serialize null shape");
    }
    std::stringstream ss(std::ios::binary | std::ios::out | std::ios::in);
    BinTools::Write(shape, ss);
    std::string str = ss.str();
    return std::vector<char>(str.begin(), str.end());
}

static TopoDS_Shape shape_from_brep_bytes(const std::vector<char>& bytes) {
    std::stringstream ss(std::string(bytes.begin(), bytes.end()), std::ios::binary | std::ios::in);
    TopoDS_Shape shape;
    BinTools::Read(shape, ss);
    if (shape.IsNull()) {
        throw std::runtime_error("Deserialized shape is null");
    }
    return shape;
}

static std::string base64_encode(const std::vector<char>& data) {
    static const char chars[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve(((data.size() + 2) / 3) * 4);
    for (size_t i = 0; i < data.size(); i += 3) {
        unsigned int b = 0;
        int n = 0;
        for (size_t j = 0; j < 3 && i + j < data.size(); ++j) {
            b = (b << 8) | static_cast<unsigned char>(data[i + j]);
            ++n;
        }
        b <<= (3 - n) * 8;
        out.push_back(chars[(b >> 18) & 0x3F]);
        out.push_back(chars[(b >> 12) & 0x3F]);
        out.push_back(n > 1 ? chars[(b >> 6) & 0x3F] : '=');
        out.push_back(n > 2 ? chars[b & 0x3F] : '=');
    }
    return out;
}

static std::vector<char> base64_decode(const std::string& in) {
    std::vector<int> map(256, -1);
    const char chars[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    for (int i = 0; i < 64; ++i) map[static_cast<unsigned char>(chars[i])] = i;
    std::vector<char> out;
    out.reserve((in.size() / 4) * 3);
    unsigned int b = 0;
    int bits = 0;
    int pad = 0;
    for (char c : in) {
        if (c == '=') {
            ++pad;
            continue;
        }
        int v = map[static_cast<unsigned char>(c)];
        if (v < 0) continue;
        b = (b << 6) | v;
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out.push_back(static_cast<char>((b >> bits) & 0xFF));
        }
    }
    return out;
}

static std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default: out += c; break;
        }
    }
    return out;
}

// Minimal CRC32 table for ZIP writing.
static const uint32_t crc32_table[256] = {
    0x00000000, 0x77073096, 0xee0e612c, 0x990951ba, 0x076dc419, 0x706af48f, 0xe963a535, 0x9e6495a3,
    0x0edb8832, 0x79dcb8a4, 0xe0d5e91e, 0x97d2d988, 0x09b64c2b, 0x7eb17cbd, 0xe7b82d07, 0x90bf1d91,
    0x1db71064, 0x6ab020f2, 0xf3b97148, 0x84be41de, 0x1adad47d, 0x6ddde4eb, 0xf4d4b551, 0x83d385c7,
    0x136c9856, 0x646ba8c0, 0xfd62f97a, 0x8a65c9ec, 0x14015c4f, 0x63066cd9, 0xfa0f3d63, 0x8d080df5,
    0x3b6e20c8, 0x4c69105e, 0xd56041e4, 0xa2677172, 0x3c03e4d1, 0x4b04d447, 0xd20d85fd, 0xa50ab56b,
    0x35b5a8fa, 0x42b2986c, 0xdbbbc9d6, 0xacbcf940, 0x32d86ce3, 0x45df5c75, 0xdcd60dcf, 0xabd13d59,
    0x26d930ac, 0x51de003a, 0xc8d75180, 0xbfd06116, 0x21b4f4b5, 0x56b3c423, 0xcfba9599, 0xb8bda50f,
    0x2802b89e, 0x5f058808, 0xc60cd9b2, 0xb10be924, 0x2f6f7c87, 0x58684c11, 0xc1611dab, 0xb6662d3d,
    0x76dc4190, 0x01db7106, 0x98d220bc, 0xefd5102a, 0x71b18589, 0x06b6b51f, 0x9fbfe4a5, 0xe8b8d433,
    0x7807c9a2, 0x0f00f934, 0x9609a88e, 0xe10e9818, 0x7f6a0dbb, 0x086d3d2d, 0x91646c97, 0xe6635c01,
    0x6b6b51f4, 0x1c6c6162, 0x856530d8, 0xf262004e, 0x6c0695ed, 0x1b01a57b, 0x8208f4c1, 0xf50fc457,
    0x65b0d9c6, 0x12b7e950, 0x8bbeb8ea, 0xfcb9887c, 0x62dd1ddf, 0x15da2d49, 0x8cd37cf3, 0xfbd44c65,
    0x4db26158, 0x3ab551ce, 0xa3bc0074, 0xd4bb30e2, 0x4adfa541, 0x3dd895d7, 0xa4d1c46d, 0xd3d6f4fb,
    0x4369e96a, 0x346ed9fc, 0xad678846, 0xda60b8d0, 0x44042d73, 0x33031de5, 0xaa0a4c5f, 0xdd0d7cc9,
    0x5005713c, 0x270241aa, 0xbe0b1010, 0xc90c2086, 0x5768b525, 0x206f85b3, 0xb966d409, 0xce61e49f,
    0x5edef90e, 0x29d9c998, 0xb0d09822, 0xc7d7a8b4, 0x59b33d17, 0x2eb40d81, 0xb7bd5c3b, 0xc0ba6cad,
    0xedb88320, 0x9abfb3b6, 0x03b6e20c, 0x74b1d29a, 0xead54739, 0x9dd277af, 0x04db2615, 0x73dc1683,
    0xe3630b12, 0x94643b84, 0x0d6d6a3e, 0x7a6a5aa8, 0xe40ecf0b, 0x9309ff9d, 0x0a00ae27, 0x7d079eb1,
    0xf00f9344, 0x8708a3d2, 0x1e01f268, 0x6906c2fe, 0xf762575d, 0x806567cb, 0x196c3671, 0x6e6b06e7,
    0xfed41b76, 0x89d32be0, 0x10da7a5a, 0x67dd4acc, 0xf9b9df6f, 0x8ebeeff9, 0x17b7be43, 0x60b08ed5,
    0xd6d6a3e8, 0xa1d1937e, 0x38d8c2c4, 0x4fdff252, 0xd1bb67f1, 0xa6bc5767, 0x3fb506dd, 0x48b2364b,
    0xd80d2bda, 0xaf0a1b4c, 0x36034af6, 0x41047a60, 0xdf60efc3, 0xa867df55, 0x316e8eef, 0x4669be79,
    0xcb61b38c, 0xbc66831a, 0x256fd2a0, 0x5268e236, 0xcc0c7795, 0xbb0b4703, 0x220216b9, 0x5505262f,
    0xc5ba3bbe, 0xb2bd0b28, 0x2bb45a92, 0x5cb36a04, 0xc2d7ffa7, 0xb5d0cf31, 0x2cd99e8b, 0x5bdeae1d,
    0x9b64c2b0, 0xec63f226, 0x756aa39c, 0x026d930a, 0x9c0906a9, 0xeb0e363f, 0x72076785, 0x05005713,
    0x95bf4a82, 0xe2b87a14, 0x7bb12bae, 0x0cb61b38, 0x92d28e9b, 0xe5d5be0d, 0x7cdcefb7, 0x0bdbdf21,
    0x86d3d2d4, 0xf1d4e242, 0x68ddb3f8, 0x1fda836e, 0x81be16cd, 0xf6b9265b, 0x6fb077e1, 0x18b74777,
    0x88085ae6, 0xff0f6a70, 0x66063bca, 0x11010b5c, 0x8f659eff, 0xf862ae69, 0x616bffd3, 0x166ccf45,
    0xa00ae278, 0xd70dd2ee, 0x4e048354, 0x3903b3c2, 0xa7672661, 0xd06016f7, 0x4969474d, 0x3e6e77db,
    0xaed16a4a, 0xd9d65adc, 0x40df0b66, 0x37d83bf0, 0xa9bcae53, 0xdebb9ec5, 0x47b2cf7f, 0x30b5ffe9,
    0xbdbdf21c, 0xcabac28a, 0x53b39330, 0x24b4a3a6, 0xbad03605, 0xcdd70693, 0x54de5729, 0x23d967bf,
    0xb3667a2e, 0xc4614ab8, 0x5d681b02, 0x2a6f2b94, 0xb40bbe37, 0xc30c8ea1, 0x5a05df1b, 0x2d02ef8d,
};

static uint32_t crc32(const char* data, size_t len) {
    uint32_t crc = 0xFFFFFFFF;
    for (size_t i = 0; i < len; ++i) {
        crc = (crc >> 8) ^ crc32_table[(crc ^ static_cast<unsigned char>(data[i])) & 0xFF];
    }
    return crc ^ 0xFFFFFFFF;
}

static void write_u32_le(char* p, uint32_t v) {
    p[0] = static_cast<char>(v & 0xFF);
    p[1] = static_cast<char>((v >> 8) & 0xFF);
    p[2] = static_cast<char>((v >> 16) & 0xFF);
    p[3] = static_cast<char>((v >> 24) & 0xFF);
}

static void write_u16_le(char* p, uint16_t v) {
    p[0] = static_cast<char>(v & 0xFF);
    p[1] = static_cast<char>((v >> 8) & 0xFF);
}

static std::string deflate_data(const char* input, size_t input_size) {
    z_stream strm{};
    strm.next_in = reinterpret_cast<Bytef*>(const_cast<char*>(input));
    strm.avail_in = static_cast<uInt>(input_size);
    int ret = deflateInit2(&strm, Z_DEFAULT_COMPRESSION, Z_DEFLATED, -15, 8, Z_DEFAULT_STRATEGY);
    if (ret != Z_OK) {
        throw std::runtime_error("Failed to initialize zlib deflate");
    }
    std::string out;
    out.reserve(input_size);
    char buffer[4096];
    do {
        strm.next_out = reinterpret_cast<Bytef*>(buffer);
        strm.avail_out = sizeof(buffer);
        ret = deflate(&strm, Z_FINISH);
        if (out.size() < strm.total_out) {
            out.append(buffer, strm.total_out - out.size());
        }
    } while (ret == Z_OK);
    deflateEnd(&strm);
    if (ret != Z_STREAM_END) {
        throw std::runtime_error("Failed to deflate data");
    }
    return out;
}

// Build a minimal .serp ZIP archive containing meta.json and document.json.
static void write_serp_zip(const std::string& filepath, const std::string& meta_json, const std::string& document_json) {
    std::ofstream f(filepath, std::ios::binary);
    if (!f) {
        throw std::runtime_error("Could not open .serp file for writing");
    }

    struct Entry {
        std::string name;
        std::string content;
        std::string compressed;
        uint32_t crc;
        uint32_t offset;
        uint32_t comp_size;
        uint32_t uncomp_size;
    };

    std::vector<Entry> entries;
    entries.push_back({"meta.json", meta_json});
    entries.push_back({"document.json", document_json});

    uint32_t local_offset = 0;
    for (auto& e : entries) {
        e.crc = crc32(e.content.data(), e.content.size());
        try {
            e.compressed = deflate_data(e.content.data(), e.content.size());
        } catch (...) {
            e.compressed = e.content; // fallback to stored
        }
        e.comp_size = static_cast<uint32_t>(e.compressed.size());
        e.uncomp_size = static_cast<uint32_t>(e.content.size());
        e.offset = local_offset;

        char local[30];
        write_u32_le(local, 0x04034B50); // local file header signature
        write_u16_le(local + 4, 20);     // version needed
        write_u16_le(local + 6, 0);      // general purpose bit flag
        write_u16_le(local + 8, 8);      // compression method: deflate
        write_u16_le(local + 10, 0);     // last mod file time
        write_u16_le(local + 12, 0);     // last mod file date
        write_u32_le(local + 14, e.crc);
        write_u32_le(local + 18, e.comp_size);
        write_u32_le(local + 22, e.uncomp_size);
        write_u16_le(local + 26, static_cast<uint16_t>(e.name.size()));
        write_u16_le(local + 28, 0);     // extra field length
        f.write(local, 30);
        f.write(e.name.data(), e.name.size());
        f.write(e.compressed.data(), e.compressed.size());
        local_offset += 30 + static_cast<uint32_t>(e.name.size()) + e.comp_size;
    }

    uint32_t cd_offset = local_offset;
    for (const auto& e : entries) {
        char central[46];
        write_u32_le(central, 0x02014B50); // central directory header signature
        write_u16_le(central + 4, 20);     // version made by
        write_u16_le(central + 6, 20);     // version needed
        write_u16_le(central + 8, 0);      // general purpose bit flag
        write_u16_le(central + 10, 8);     // compression method: deflate
        write_u16_le(central + 12, 0);     // last mod file time
        write_u16_le(central + 14, 0);     // last mod file date
        write_u32_le(central + 16, e.crc);
        write_u32_le(central + 20, e.comp_size);
        write_u32_le(central + 24, e.uncomp_size);
        write_u16_le(central + 28, static_cast<uint16_t>(e.name.size()));
        write_u16_le(central + 30, 0);     // extra field length
        write_u16_le(central + 32, 0);     // comment length
        write_u16_le(central + 34, 0);     // disk number start
        write_u16_le(central + 36, 0);     // internal file attributes
        write_u32_le(central + 38, 0);     // external file attributes
        write_u32_le(central + 42, e.offset);
        f.write(central, 46);
        f.write(e.name.data(), e.name.size());
        local_offset += 46 + static_cast<uint32_t>(e.name.size());
    }

    uint32_t cd_size = local_offset - cd_offset;
    char eocd[22];
    write_u32_le(eocd, 0x06054B50); // EOCD signature
    write_u16_le(eocd + 4, 0);      // disk number
    write_u16_le(eocd + 6, 0);      // disk with central directory
    write_u16_le(eocd + 8, static_cast<uint16_t>(entries.size()));
    write_u16_le(eocd + 10, static_cast<uint16_t>(entries.size()));
    write_u32_le(eocd + 12, cd_size);
    write_u32_le(eocd + 16, cd_offset);
    write_u16_le(eocd + 20, 0);     // comment length
    f.write(eocd, 22);
}

static std::string build_meta_json(int object_count) {
    std::string meta = "{\n";
    meta += "  \"format\": \"serpentine3d\",\n";
    meta += "  \"version\": 2,\n";
    meta += "  \"objects\": " + std::to_string(object_count) + ",\n";
    meta += "  \"layouts\": 0\n";
    meta += "}\n";
    return meta;
}

static std::string build_document_json(const std::vector<std::pair<std::string, std::string>>& objects) {
    std::string doc = "{\n";
    doc += "  \"format\": \"serpentine3d\",\n";
    doc += "  \"version\": 2,\n";
    doc += "  \"objects\": [\n";
    for (size_t i = 0; i < objects.size(); ++i) {
        const auto& [name, b64] = objects[i];
        std::string id_str = std::to_string(i + 1);
        if (i > 0) doc += ",\n";
        doc += "    {\"id\": \"" + id_str + "\", \"name\": \"" + json_escape(name) + "\", \"layer\": \"default\", \"visible\": true, \"locked\": false, \"brep\": \"" + b64 + "\"}";
    }
    doc += "\n  ]\n";
    doc += "}\n";
    return doc;
}

std::tuple<bool, std::string> export_serp(int shape_id, const std::string& filepath) {
    if (!has_shape(shape_id)) {
        return {false, "Shape ID not found in registry"};
    }
    try {
        TopoDS_Shape shape = get_shape(shape_id);
        auto bytes = shape_to_brep_bytes(shape);
        std::string b64 = base64_encode(bytes);

        std::vector<std::pair<std::string, std::string>> objects;
        objects.emplace_back("Hippo3D Export", b64);
        std::string doc = build_document_json(objects);
        std::string meta = build_meta_json(1);
        write_serp_zip(filepath, meta, doc);
        return {true, "Exported Serpentine3D: " + filepath};
    } catch (const std::exception& e) {
        return {false, std::string("Serp export error: ") + e.what()};
    }
}

std::tuple<bool, std::string> export_serp_multi(const std::vector<int>& shape_ids, const std::string& filepath) {
    if (shape_ids.empty()) {
        return {false, "No shapes selected for Serp export"};
    }
    try {
        std::vector<std::pair<std::string, std::string>> objects;
        for (size_t i = 0; i < shape_ids.size(); ++i) {
            int shape_id = shape_ids[i];
            if (!has_shape(shape_id)) {
                continue;
            }
            TopoDS_Shape shape = get_shape(shape_id);
            auto bytes = shape_to_brep_bytes(shape);
            std::string b64 = base64_encode(bytes);
            std::string id_str = std::to_string(i + 1);
            objects.emplace_back("Hippo3D Export " + id_str, b64);
        }
        if (objects.empty()) {
            return {false, "No valid shapes selected for Serp export"};
        }
        std::string doc = build_document_json(objects);
        std::string meta = build_meta_json(static_cast<int>(objects.size()));
        write_serp_zip(filepath, meta, doc);
        return {true, "Exported Serpentine3D: " + filepath};
    } catch (const std::exception& e) {
        return {false, std::string("Serp export error: ") + e.what()};
    }
}

// Read a file fully into memory.
static std::vector<char> read_file_bytes(const std::string& filepath) {
    std::ifstream f(filepath, std::ios::binary);
    if (!f) {
        throw std::runtime_error("Could not open .serp file for reading");
    }
    f.seekg(0, std::ios::end);
    size_t size = static_cast<size_t>(f.tellg());
    f.seekg(0, std::ios::beg);
    std::vector<char> buffer(size);
    if (size > 0) {
        f.read(buffer.data(), static_cast<std::streamsize>(size));
    }
    return buffer;
}

static bool is_zip(const std::vector<char>& data) {
    return data.size() >= 4 &&
           static_cast<unsigned char>(data[0]) == 0x50 &&
           static_cast<unsigned char>(data[1]) == 0x4B &&
           static_cast<unsigned char>(data[2]) == 0x03 &&
           static_cast<unsigned char>(data[3]) == 0x04;
}

// Little-endian helpers.
static uint32_t read_u32_le(const char* p) {
    return static_cast<uint32_t>(static_cast<unsigned char>(p[0])) |
           (static_cast<uint32_t>(static_cast<unsigned char>(p[1])) << 8) |
           (static_cast<uint32_t>(static_cast<unsigned char>(p[2])) << 16) |
           (static_cast<uint32_t>(static_cast<unsigned char>(p[3])) << 24);
}
static uint16_t read_u16_le(const char* p) {
    return static_cast<uint16_t>(static_cast<unsigned char>(p[0])) |
           (static_cast<uint16_t>(static_cast<unsigned char>(p[1])) << 8);
}

// Minimal ZIP reader: extract the uncompressed contents of a named entry.
// Only supports deflate (compression method 8) and stored (compression method 0).
static std::string extract_zip_entry(const std::vector<char>& data, const std::string& target_name) {
    const size_t size = data.size();
    if (size < 22) {
        throw std::runtime_error("ZIP file too small");
    }

    // Find End of Central Directory Record (EOCD).
    size_t eocd_pos = std::string::npos;
    for (size_t i = size - 22; i + 22 <= size; --i) {
        if (static_cast<unsigned char>(data[i]) == 0x50 &&
            static_cast<unsigned char>(data[i + 1]) == 0x4B &&
            static_cast<unsigned char>(data[i + 2]) == 0x05 &&
            static_cast<unsigned char>(data[i + 3]) == 0x06) {
            eocd_pos = i;
            break;
        }
        if (i == 0) break;
    }
    if (eocd_pos == std::string::npos) {
        throw std::runtime_error("Could not find ZIP central directory");
    }

    const char* eocd = data.data() + eocd_pos;
    uint16_t cd_entries = read_u16_le(eocd + 10);
    uint32_t cd_size = read_u32_le(eocd + 12);
    uint32_t cd_offset = read_u32_le(eocd + 16);

    if (cd_offset + cd_size > size) {
        throw std::runtime_error("Invalid ZIP central directory offset");
    }

    // Scan central directory for the target entry.
    size_t p = cd_offset;
    uint32_t local_offset = 0;
    uint32_t comp_size = 0;
    uint32_t uncomp_size = 0;
    uint16_t method = 0;
    bool found = false;

    for (uint16_t entry = 0; entry < cd_entries && p + 46 <= size; ++entry) {
        const char* hdr = data.data() + p;
        if (read_u32_le(hdr) != 0x02014B50) {
            break;
        }
        uint16_t name_len = read_u16_le(hdr + 28);
        uint16_t extra_len = read_u16_le(hdr + 30);
        uint16_t comment_len = read_u16_le(hdr + 32);
        if (p + 46 + name_len > size) {
            break;
        }
        std::string name(data.data() + p + 46, name_len);
        if (name == target_name) {
            found = true;
            method = read_u16_le(hdr + 10);
            comp_size = read_u32_le(hdr + 20);
            uncomp_size = read_u32_le(hdr + 24);
            local_offset = read_u32_le(hdr + 42);
            break;
        }
        p += 46 + name_len + extra_len + comment_len;
    }

    if (!found) {
        throw std::runtime_error("ZIP entry not found: " + target_name);
    }

    // Read local file header to skip variable-length fields.
    if (local_offset + 30 > size) {
        throw std::runtime_error("Invalid local file header offset");
    }
    const char* lhdr = data.data() + local_offset;
    if (read_u32_le(lhdr) != 0x04034B50) {
        throw std::runtime_error("Invalid local file header signature");
    }
    uint16_t local_name_len = read_u16_le(lhdr + 26);
    uint16_t local_extra_len = read_u16_le(lhdr + 28);
    size_t data_offset = local_offset + 30 + local_name_len + local_extra_len;
    if (data_offset + comp_size > size) {
        throw std::runtime_error("Invalid compressed data bounds");
    }

    if (method == 0) {
        // Stored
        return std::string(data.data() + data_offset, comp_size);
    } else if (method == 8) {
        // Deflate
        std::string out;
        out.resize(uncomp_size);
        z_stream strm{};
        strm.next_in = reinterpret_cast<Bytef*>(const_cast<char*>(data.data() + data_offset));
        strm.avail_in = comp_size;
        strm.next_out = reinterpret_cast<Bytef*>(&out[0]);
        strm.avail_out = uncomp_size;
        if (inflateInit2(&strm, -15) != Z_OK) {
            throw std::runtime_error("Failed to initialize zlib inflate");
        }
        int ret = inflate(&strm, Z_FINISH);
        if (ret != Z_STREAM_END) {
            inflateEnd(&strm);
            throw std::runtime_error("Failed to inflate ZIP entry");
        }
        inflateEnd(&strm);
        return out;
    } else {
        throw std::runtime_error("Unsupported ZIP compression method");
    }
}

// Read the .serp document text, handling both raw JSON and ZIP-wrapped files.
static std::string read_serp_document_text(const std::string& filepath) {
    std::vector<char> data = read_file_bytes(filepath);
    if (is_zip(data)) {
        return extract_zip_entry(data, "document.json");
    }
    return std::string(data.begin(), data.end());
}

// Locate the JSON "objects" array and extract each object's brep and name.
// Handles nested braces inside the array and ignores the outer document.
static std::vector<std::pair<std::string, std::string>> extract_serp_objects(const std::string& text) {
    std::vector<std::pair<std::string, std::string>> objects;

    auto skip_whitespace = [&](size_t p) -> size_t {
        while (p < text.size() && (text[p] == ' ' || text[p] == '\t' || text[p] == '\n' || text[p] == '\r')) {
            ++p;
        }
        return p;
    };

    auto find_quoted_key = [&](size_t start, const std::string& key) -> size_t {
        std::string quoted = "\"" + key + "\"";
        size_t pos = text.find(quoted, start);
        // Ensure it is actually a key: preceding char must not be an escape or part of another string.
        while (pos != std::string::npos) {
            if (pos == 0 || text[pos - 1] != '\\') {
                return pos;
            }
            pos = text.find(quoted, pos + 1);
        }
        return std::string::npos;
    };

    auto find_matching_brace = [&](size_t open_pos) -> size_t {
        if (open_pos >= text.size() || text[open_pos] != '{') {
            return std::string::npos;
        }
        int depth = 1;
        bool in_string = false;
        for (size_t i = open_pos + 1; i < text.size(); ++i) {
            char c = text[i];
            if (in_string) {
                if (c == '\\') {
                    ++i; // skip escaped char
                    continue;
                }
                if (c == '"') {
                    in_string = false;
                }
                continue;
            }
            if (c == '"') {
                in_string = true;
            } else if (c == '{') {
                ++depth;
            } else if (c == '}') {
                --depth;
                if (depth == 0) {
                    return i;
                }
            }
        }
        return std::string::npos;
    };

    auto extract_quoted_value_after_colon = [&](size_t key_pos) -> std::pair<std::string, size_t> {
        // key_pos points to the opening quote of the key.
        size_t p = skip_whitespace(key_pos + 1); // skip key opening quote? no, key_pos is at quote, need to skip the whole quoted key.
        // Find the closing quote of the key.
        p = text.find('"', key_pos + 1);
        if (p == std::string::npos) {
            return {"", std::string::npos};
        }
        p = skip_whitespace(p + 1);
        if (p >= text.size() || text[p] != ':') {
            return {"", std::string::npos};
        }
        p = skip_whitespace(p + 1);
        if (p >= text.size() || text[p] != '"') {
            return {"", p};
        }
        // Extract quoted string value.
        size_t q1 = p;
        size_t q2 = text.find('"', q1 + 1);
        while (q2 != std::string::npos && text[q2 - 1] == '\\') {
            q2 = text.find('"', q2 + 1);
        }
        if (q2 == std::string::npos) {
            return {"", std::string::npos};
        }
        return {text.substr(q1 + 1, q2 - q1 - 1), q2 + 1};
    };

    // 1. Find the "objects" array.
    size_t objects_key = find_quoted_key(0, "objects");
    if (objects_key == std::string::npos) {
        return objects;
    }
    size_t after_key = text.find('"', objects_key + 1);
    if (after_key == std::string::npos) {
        return objects;
    }
    size_t array_start = skip_whitespace(after_key + 1);
    if (array_start >= text.size() || text[array_start] != ':') {
        return objects;
    }
    array_start = skip_whitespace(array_start + 1);
    if (array_start >= text.size() || text[array_start] != '[') {
        return objects;
    }
    ++array_start; // skip '['

    // 2. Scan the array for object entries, handling nesting.
    size_t pos = array_start;
    while (pos < text.size()) {
        pos = skip_whitespace(pos);
        if (pos >= text.size() || text[pos] == ']') {
            break;
        }
        if (text[pos] != '{') {
            ++pos;
            continue;
        }

        size_t obj_start = pos;
        size_t obj_end = find_matching_brace(obj_start);
        if (obj_end == std::string::npos) {
            break;
        }

        std::string brep;
        std::string name = "Hippo3D_OCC_Serp";

        // Extract brep within this object.
        size_t brep_key = find_quoted_key(obj_start + 1, "brep");
        if (brep_key != std::string::npos && brep_key < obj_end) {
            auto [val, next] = extract_quoted_value_after_colon(brep_key);
            brep = val;
        }

        // Extract name within this object.
        size_t name_key = find_quoted_key(obj_start + 1, "name");
        if (name_key != std::string::npos && name_key < obj_end) {
            auto [val, next] = extract_quoted_value_after_colon(name_key);
            if (!val.empty()) {
                name = val;
            }
        }

        if (!brep.empty()) {
            objects.emplace_back(name, brep);
        }
        pos = obj_end + 1;
        pos = skip_whitespace(pos);
        if (pos < text.size() && text[pos] == ',') {
            ++pos;
        }
    }

    return objects;
}

std::vector<int> import_serp(const std::string& filepath) {
    std::vector<int> shape_ids;
    try {
        std::string text = read_serp_document_text(filepath);
        auto objects = extract_serp_objects(text);
        if (objects.empty()) {
            throw std::runtime_error("No BREP objects found in .serp file.");
        }
        for (const auto& [name, b64] : objects) {
            auto bytes = base64_decode(b64);
            TopoDS_Shape shape = shape_from_brep_bytes(bytes);
            int shape_id = register_shape(shape);
            shape_ids.push_back(shape_id);
        }
    } catch (const std::runtime_error&) {
        throw;
    } catch (const std::exception& e) {
        throw std::runtime_error(std::string("Serp import exception: ") + e.what());
    }
    return shape_ids;
}
