from flask import Flask, request, jsonify
import xml.etree.ElementTree as ET
import uuid
import requests
import os
from xml.sax.saxutils import escape as xml_escape
from typing import Dict, Any, Optional, List
from flask_cors import CORS  # Added for Roblox compatibility

app = Flask(__name__)

# Enable CORS for all routes (important for Roblox client)
CORS(app, resources={r"/*": {"origins": "*"}})

# ====================== CONFIG & LIMITS ======================
MAX_JSON_SIZE = 5 * 1024 * 1024  # 5MB
MAX_INSTANCES = 5000
MAX_CHILD_DEPTH = 50

# ====================== HELPERS ======================
def esc(v):
    return xml_escape(str(v))

def new_ref():
    return f"RBX{uuid.uuid4().hex.upper()[:32]}"

def num_list(v, n, default):
    if not isinstance(v, list):
        return default[:n]
    return [v[i] if i < len(v) else default[i] for i in range(n)]

def normalize_color(color):
    if not isinstance(color, list) or len(color) != 3:
        return [1.0, 1.0, 1.0]
    if max(color) > 1.0:  # 0-255 → 0-1
        return [c / 255.0 for c in color]
    return color

# ====================== TOKEN MAPPINGS ======================
def token_material(v):
    if not v: 
        return "256"
    s = str(v).lower().replace(" ", "").replace("_", "").replace("-", "")
    mapping = {
        "plastic": "256", "smoothplastic": "272", "neon": "288",
        "wood": "512", "woodplanks": "528", "marble": "784", "basalt": "788",
        "slate": "800", "crackedlava": "804", "concrete": "816", "limestone": "820",
        "granite": "832", "pavement": "836", "brick": "848", "pebble": "864",
        "sand": "880", "ice": "896", "cobblestone": "912", "rock": "928",
        "grass": "1024", "corrodedmetal": "1040", "diamondplate": "1056",
        "foil": "1072", "metal": "1072", "fabric": "1088", "glacier": "1520",
    }
    return mapping.get(s, "256")

def token_shape(v):
    if not v: 
        return "1"
    s = str(v).lower()
    if any(x in s for x in ["ball", "sphere"]): 
        return "0"
    if "cylinder" in s: 
        return "2"
    return "1"

def token_face(v):
    s = str(v).lower()
    mapping = {"front": "5", "back": "2", "left": "3", "right": "0", "top": "1", "bottom": "4"}
    return mapping.get(s, "5")

# ====================== BUILDERS ======================
def build_special_mesh(data: dict, parent_ref: str) -> ET.Element:
    ref = new_ref()
    item = ET.Element("Item", {"class": "SpecialMesh", "referent": ref})
    props = ET.SubElement(item, "Properties")
    ET.SubElement(props, "string", {"name": "Name"}).text = esc(data.get("Name", "Mesh"))
    ET.SubElement(props, "Ref", {"name": "Parent"}).text = parent_ref

    if mesh_id := data.get("MeshId"):
        content = ET.SubElement(props, "Content", {"name": "MeshId"})
        ET.SubElement(content, "url").text = esc(mesh_id)
    if texture_id := data.get("TextureId"):
        content = ET.SubElement(props, "Content", {"name": "TextureId"})
        ET.SubElement(content, "url").text = esc(texture_id)

    scale = num_list(data.get("Scale", [1,1,1]), 3, [1,1,1])
    scale_el = ET.SubElement(props, "Vector3", {"name": "Scale"})
    for tag, val in zip(["X", "Y", "Z"], scale):
        ET.SubElement(scale_el, tag).text = str(val)
    return item

def build_instance(data: dict, parent_ref: Optional[str] = None, depth: int = 0, count: List[int] = None) -> Optional[ET.Element]:
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > MAX_INSTANCES or depth > MAX_CHILD_DEPTH:
        raise ValueError("Instance limit or depth exceeded")

    cls = data.get("ClassName", "Part")
    ref = new_ref()
    item = ET.Element("Item", {"class": cls, "referent": ref})
    props = ET.SubElement(item, "Properties")

    ET.SubElement(props, "string", {"name": "Name"}).text = esc(data.get("Name", cls))
    if parent_ref:
        ET.SubElement(props, "Ref", {"name": "Parent"}).text = parent_ref

    if cls in ["Part", "MeshPart"]:
        size = num_list(data.get("Size", [4,4,4]), 3, [4,4,4])
        color = normalize_color(data.get("Color", [163,162,165]))

        # Size
        size_el = ET.SubElement(props, "Vector3", {"name": "Size"})
        for tag, val in zip(["X", "Y", "Z"], size):
            ET.SubElement(size_el, tag).text = str(val)

        # CFrame
        if "CFrame" in data:
            cf = num_list(data["CFrame"], 12, [0]*12)
            cf_el = ET.SubElement(props, "CoordinateFrame", {"name": "CFrame"})
            tags = ["X","Y","Z","R00","R01","R02","R10","R11","R12","R20","R21","R22"]
            for tag, val in zip(tags, cf):
                ET.SubElement(cf_el, tag).text = str(val)
        elif "Position" in data:
            pos = num_list(data["Position"], 3, [0,0,0])
            cf_el = ET.SubElement(props, "CoordinateFrame", {"name": "CFrame"})
            for tag, val in zip(["X","Y","Z"], pos):
                ET.SubElement(cf_el, tag).text = str(val)

        # Color
        col_el = ET.SubElement(props, "Color3", {"name": "Color"})
        for tag, val in zip(["R","G","B"], color):
            ET.SubElement(col_el, tag).text = str(val)

        ET.SubElement(props, "token", {"name": "Material"}).text = token_material(data.get("Material"))
        ET.SubElement(props, "bool", {"name": "Anchored"}).text = str(data.get("Anchored", True)).lower()
        ET.SubElement(props, "bool", {"name": "CanCollide"}).text = str(data.get("CanCollide", True)).lower()

        if cls == "Part":
            ET.SubElement(props, "token", {"name": "Shape"}).text = token_shape(data.get("Shape"))

        if cls == "MeshPart" and data.get("MeshId"):
            content = ET.SubElement(props, "Content", {"name": "MeshId"})
            ET.SubElement(content, "url").text = esc(data.get("MeshId"))

    # SpecialMesh support
    if cls == "SpecialMesh" or data.get("SpecialMesh"):
        mesh_data = data.get("SpecialMesh", data)
        mesh_item = build_special_mesh(mesh_data, ref)
        item.append(mesh_item)

    # Children
    for child in data.get("Children", []):
        child_item = build_instance(child, ref, depth + 1, count)
        if child_item:
            item.append(child_item)

    return item

def json_to_rbxmx(json_data: Dict[str, Any], display_name: str = "Studio Creation") -> str:
    if len(str(json_data).encode('utf-8')) > MAX_JSON_SIZE:
        raise ValueError("JSON too large")

    root = ET.Element("roblox", {"version": "4"})
    ET.SubElement(root, "Meta", {"name": "ExplicitAutoJoints"}).text = "true"

    model = ET.Element("Item", {"class": "Model", "referent": new_ref()})
    props = ET.SubElement(model, "Properties")
    ET.SubElement(props, "string", {"name": "Name"}).text = esc(display_name)

    count = [0]
    main_item = build_instance(json_data if isinstance(json_data, dict) else (json_data[0] if isinstance(json_data, list) and json_data else {}))
    if main_item:
        model.append(main_item)

    root.append(model)

    output_file = "generated_model.rbxmx"
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return output_file

# ====================== MAIN ROUTE ======================
@app.route("/publish", methods=["POST"])
def publish():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        # Support multiple possible keys for compatibility
        json_payload = data.get("json_data") or data.get("instances")
        api_key = data.get("apiKey") or data.get("api_key")
        display_name = data.get("displayName", data.get("assetName", "Studio Creation"))
        description = data.get("description", "Uploaded via Studio Creations Lite")
        user_id = data.get("userId") or data.get("user_id")

        if not api_key:
            return jsonify({"error": "apiKey is required"}), 400
        if not json_payload:
            return jsonify({"error": "json_data or instances is required"}), 400

        # Generate RBXMX
        rbxmx_path = json_to_rbxmx(json_payload, display_name)

        # Prepare upload
        request_payload = {
            "assetType": "Model",
            "displayName": display_name,
            "description": description,
            "creationContext": {"creator": {"userId": user_id or 0}}
        }

        files = {
            "request": (None, str(request_payload), "application/json"),  # Simplified
            "fileContent": ("model.rbxmx", open(rbxmx_path, "rb"), "model/x-rbxm")
        }

        resp = requests.post(
            "https://apis.roblox.com/assets/v1/assets",
            headers={"x-api-key": api_key},
            files=files
        )

        # Cleanup
        if os.path.exists(rbxmx_path):
            os.remove(rbxmx_path)

        if resp.status_code in (200, 202):
            return jsonify({"success": True, "response": resp.json()})
        else:
            return jsonify({
                "error": "Roblox upload failed", 
                "status": resp.status_code, 
                "details": resp.text
            }), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000, debug=False)
