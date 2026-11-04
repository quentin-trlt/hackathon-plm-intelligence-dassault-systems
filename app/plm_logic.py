"""
Core Business Logic and Hierarchical Query Engine for PLM Intelligence.
Module: plm_logic.py
"""

from typing import Dict, Any, List, Optional, Union
from collections import deque
from plm_parser import PLMDatabase


class PLMEngine:
    """
    Core backend logic for PLM BOM tree building, Where-Used change impact analysis,
    and mass rollup aggregations.
    """

    def __init__(self, db: Optional[PLMDatabase] = None, xml_file: str = "XPDMXML_ECAR_PLM.xml"):
        if db is not None:
            self.db = db
        else:
            import os
            candidates = [
                xml_file,
                os.path.join(os.path.dirname(__file__), xml_file),
                os.path.join(os.path.dirname(__file__), "..", "data", "raw", "XPDMXML_ECAR_PLM.xml"),
                os.path.join(os.path.dirname(__file__), "data", "raw", "XPDMXML_ECAR_PLM.xml"),
                os.path.join("data", "raw", "XPDMXML_ECAR_PLM.xml")
            ]
            resolved_xml = next((c for c in candidates if os.path.exists(c)), xml_file)
            self.db = PLMDatabase()
            self.db.parse_xml(resolved_xml)

    def _resolve_product_record(self, product_id: str) -> Optional[Dict[str, Any]]:
        """
        Resolve a product identifier that may be an internal xml_id (e.g. 'ID_3')
        or a formal PLM ID (e.g. 'prd-12535825-00000081').
        """
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE xml_id = ? OR plm_id = ?", (product_id, product_id))
        row = cursor.fetchone()
        return dict(row) if row else None

    def build_bom_tree(self, root_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Perform a top-down traversal from root_id downwards.
        If no root_id is provided, automatically identify the top-level assembly
        (the product that does not appear as a child in any ProductInst relationship).

        :param root_id: xml_id or plm_id of the root assembly (optional)
        :return: Nested dictionary representing the full BOM hierarchy
        """
        cursor = self.db.conn.cursor()

        if root_id is None:
            # Find root assembly that has no parent
            cursor.execute("""
                SELECT xml_id FROM products 
                WHERE xml_id NOT IN (SELECT DISTINCT child_xml_id FROM product_instances)
                LIMIT 1
            """)
            root_row = cursor.fetchone()
            if root_row:
                resolved_root_id = root_row["xml_id"]
            else:
                resolved_root_id = self.db.root_ref or "ID_0"
        else:
            prod = self._resolve_product_record(root_id)
            if not prod:
                raise ValueError(f"Product '{root_id}' not found in PLM database.")
            resolved_root_id = prod["xml_id"]

        root_prod = self._resolve_product_record(resolved_root_id)
        if not root_prod:
            raise ValueError(f"Root product with ID '{resolved_root_id}' could not be loaded.")

        def _traverse(node_xml_id: str, visited: set) -> Dict[str, Any]:
            prod_info = self._resolve_product_record(node_xml_id)
            if not prod_info:
                return {}

            # Prevent infinite loops in cyclic dependencies
            if node_xml_id in visited:
                return {
                    **prod_info,
                    "is_leaf": True,
                    "children": [],
                    "cycle_detected": True
                }

            # Query all direct children from product_instances
            cursor.execute("""
                SELECT instance_xml_id, instance_name, child_xml_id, child_order
                FROM product_instances
                WHERE parent_xml_id = ?
                ORDER BY child_order ASC, instance_name ASC
            """, (node_xml_id,))
            child_rows = cursor.fetchall()

            new_visited = visited | {node_xml_id}
            children = []
            for row in child_rows:
                child_tree = _traverse(row["child_xml_id"], new_visited)
                # Attach instance-specific context
                child_tree["instance_xml_id"] = row["instance_xml_id"]
                child_tree["instance_name"] = row["instance_name"]
                child_tree["child_order"] = row["child_order"]
                children.append(child_tree)

            return {
                "xml_id": prod_info["xml_id"],
                "plm_id": prod_info["plm_id"],
                "name": prod_info["name"],
                "revision": prod_info["revision"],
                "revision_index": prod_info["revision_index"],
                "maturity": prod_info["maturity"],
                "mass": prod_info["mass"],
                "material": prod_info["material"],
                "owner": prod_info["owner"],
                "project": prod_info["project"],
                "is_leaf": len(children) == 0,
                "children_count": len(children),
                "children": children
            }

        return _traverse(resolved_root_id, set())

    def get_where_used(self, product_id: str) -> List[Dict[str, Any]]:
        """
        Bottom-up traversal for Change Impact Analysis.
        Finds all parent assemblies that use the given component (tracing from
        <Instancing> through <Owned> recursively up to the top-level assembly).

        :param product_id: xml_id or plm_id of the component to analyze
        :return: List of all affected parent assemblies with hierarchy level and path
        """
        target_prod = self._resolve_product_record(product_id)
        if not target_prod:
            raise ValueError(f"Product '{product_id}' not found in PLM database.")

        target_xml_id = target_prod["xml_id"]
        cursor = self.db.conn.cursor()

        affected_parents: List[Dict[str, Any]] = []
        visited_instances = set()
        queue = deque([(target_xml_id, target_prod["name"], 1, [target_prod["name"]])])

        while queue:
            current_child_id, current_child_name, level, path = queue.popleft()

            # Find all instances where current_child_id is used as a child (<Instancing>)
            cursor.execute("""
                SELECT pi.instance_xml_id, pi.instance_name, pi.parent_xml_id,
                       p.plm_id as parent_plm_id, p.name as parent_name,
                       p.revision as parent_revision, p.maturity as parent_maturity,
                       p.mass as parent_mass, p.material as parent_material,
                       p.owner as parent_owner
                FROM product_instances pi
                JOIN products p ON pi.parent_xml_id = p.xml_id
                WHERE pi.child_xml_id = ?
            """, (current_child_id,))

            rows = cursor.fetchall()
            for r in rows:
                inst_id = r["instance_xml_id"]
                if inst_id in visited_instances:
                    continue
                visited_instances.add(inst_id)

                parent_xml_id = r["parent_xml_id"]
                parent_name = r["parent_name"]
                new_path = path + [parent_name]

                affected_entry = {
                    "level": level,
                    "parent_xml_id": parent_xml_id,
                    "parent_plm_id": r["parent_plm_id"],
                    "parent_name": parent_name,
                    "parent_revision": r["parent_revision"],
                    "parent_maturity": r["parent_maturity"],
                    "parent_mass": r["parent_mass"],
                    "parent_material": r["parent_material"],
                    "parent_owner": r["parent_owner"],
                    "via_instance_id": inst_id,
                    "via_instance_name": r["instance_name"],
                    "impact_path": " -> ".join(new_path)
                }
                affected_parents.append(affected_entry)

                # Continue upward traversal to find higher ancestors
                queue.append((parent_xml_id, parent_name, level + 1, new_path))

        return affected_parents

    def calculate_total_mass(self, product_id: str) -> Dict[str, Any]:
        """
        Calculate the total mass of a product or assembly.
        Recursively sums the mass of all children instances.
        Also returns comparison with declared assembly mass and component breakdown.

        :param product_id: xml_id or plm_id of the component/assembly
        :return: Dict containing computed mass, declared mass, and component breakdown
        """
        target_prod = self._resolve_product_record(product_id)
        if not target_prod:
            raise ValueError(f"Product '{product_id}' not found in PLM database.")

        cursor = self.db.conn.cursor()

        def _calc_recursive(xml_id: str) -> Tuple[float, List[Dict[str, Any]]]:
            # Check if this node has children
            cursor.execute("""
                SELECT pi.instance_xml_id, pi.instance_name, pi.child_xml_id,
                       p.plm_id, p.name, p.mass, p.material
                FROM product_instances pi
                JOIN products p ON pi.child_xml_id = p.xml_id
                WHERE pi.parent_xml_id = ?
            """, (xml_id,))
            children = cursor.fetchall()

            if not children:
                # Leaf part: its recursive mass contribution is its own declared mass
                cursor.execute("SELECT mass FROM products WHERE xml_id = ?", (xml_id,))
                leaf_row = cursor.fetchone()
                return (leaf_row["mass"] if leaf_row else 0.0, [])

            total_sum = 0.0
            breakdown = []
            for child in children:
                c_xml_id = child["child_xml_id"]
                c_mass, _ = _calc_recursive(c_xml_id)
                total_sum += c_mass
                breakdown.append({
                    "child_xml_id": c_xml_id,
                    "child_plm_id": child["plm_id"],
                    "child_name": child["name"],
                    "instance_name": child["instance_name"],
                    "material": child["material"],
                    "mass": round(c_mass, 4)
                })

            return (total_sum, breakdown)

        from typing import Tuple
        computed_sum, breakdown = _calc_recursive(target_prod["xml_id"])

        is_assembly = len(breakdown) > 0
        final_mass = computed_sum if is_assembly else target_prod["mass"]

        return {
            "product_id": target_prod["plm_id"],
            "xml_id": target_prod["xml_id"],
            "name": target_prod["name"],
            "is_assembly": is_assembly,
            "declared_mass": round(target_prod["mass"], 4),
            "calculated_total_mass": round(final_mass, 4),
            "delta_mass": round(abs(target_prod["mass"] - final_mass), 4) if is_assembly else 0.0,
            "subcomponent_count": len(breakdown),
            "direct_children_breakdown": breakdown
        }


# Module-level convenience functions
_engine = None

def get_engine() -> PLMEngine:
    global _engine
    if _engine is None:
        _engine = PLMEngine()
    return _engine

def build_bom_tree(root_id: Optional[str] = None) -> Dict[str, Any]:
    return get_engine().build_bom_tree(root_id)

def get_where_used(product_id: str) -> List[Dict[str, Any]]:
    return get_engine().get_where_used(product_id)

def calculate_total_mass(product_id: str) -> Dict[str, Any]:
    return get_engine().calculate_total_mass(product_id)


if __name__ == "__main__":
    engine = PLMEngine()

    print("==================================================")
    print("PLM CORE LOGIC TEST SUITE")
    print("==================================================")

    # 1. Test build_bom_tree
    print("\n1. Testing build_bom_tree(root_id=None):")
    bom = engine.build_bom_tree()
    print(f"Top-level root: {bom['name']} ({bom['plm_id']})")
    print(f"Direct children count: {bom['children_count']}")
    for child in bom["children"]:
        print(f"  -> [{child['instance_name']}] {child['name']} ({child['plm_id']}) - Mass: {child['mass']} kg")

    # 2. Test calculate_total_mass
    print("\n2. Testing calculate_total_mass for root:")
    mass_info = engine.calculate_total_mass("ID_0")
    print(f"Product: {mass_info['name']}")
    print(f"Declared Mass: {mass_info['declared_mass']} kg")
    print(f"Calculated Recursive Mass: {mass_info['calculated_total_mass']} kg")
    print(f"Delta: {mass_info['delta_mass']} kg")

    # 3. Test get_where_used for a random child component
    test_child_id = "ID_3"  # ECAR-3D_HSH_BEA3
    print(f"\n3. Testing get_where_used('{test_child_id}'):")
    where_used_results = engine.get_where_used(test_child_id)
    print(f"Total Affected Parent Assemblies: {len(where_used_results)}")
    for item in where_used_results:
        print(f"  [Level {item['level']}] Via Instance: {item['via_instance_name']} -> Parent: {item['parent_name']} ({item['parent_plm_id']})")
        print(f"     Breadcrumb: {item['impact_path']}")

    # 4. Test Error Handling
    print("\n4. Testing Error Handling for invalid product_id:")
    try:
        engine.get_where_used("INVALID_ID_999")
    except ValueError as e:
        print(f"Successfully caught expected error: {e}")
