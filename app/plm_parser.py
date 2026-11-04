"""
PLM XML Parser and Database Setup for 3DEXPERIENCE XPDMXML Data.
Module: plm_parser.py
"""

import xml.etree.ElementTree as ET
import sqlite3
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd


class PLMDatabase:
    """
    Parses 3DEXPERIENCE XPDMXML files and stores data in an optimized SQLite database
    and structured Pandas DataFrames for fast BOM and impact graph queries.
    """

    XML_NAMESPACE = {'x': 'http://www.3ds.com/xsd/XPDMXML'}

    def __init__(self, db_path: str = ":memory:"):
        """
        Initialize the SQLite database.
        :param db_path: Path to SQLite DB file or ':memory:' for an in-memory database.
        """
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.root_ref: Optional[str] = None
        self._init_schema()

    def _init_schema(self) -> None:
        """Create normalized database schema and indexes."""
        cursor = self.conn.cursor()

        # Products table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS products (
                xml_id TEXT PRIMARY KEY,
                plm_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                revision TEXT,
                revision_index INTEGER,
                revision_family TEXT,
                maturity TEXT,
                lifecycle TEXT,
                mass REAL DEFAULT 0.0,
                material TEXT,
                owner TEXT,
                organization TEXT,
                project TEXT,
                time_created TEXT,
                time_modified TEXT
            )
        """)

        # Product instances (Parent-Child relationships)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_instances (
                instance_xml_id TEXT PRIMARY KEY,
                instance_name TEXT NOT NULL,
                parent_xml_id TEXT NOT NULL,
                child_xml_id TEXT NOT NULL,
                child_order REAL,
                FOREIGN KEY (parent_xml_id) REFERENCES products(xml_id),
                FOREIGN KEY (child_xml_id) REFERENCES products(xml_id)
            )
        """)

        # Optimization indexes for fast hierarchical queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_inst_parent ON product_instances(parent_xml_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_inst_child ON product_instances(child_xml_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prod_plm_id ON products(plm_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prod_name ON products(name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prod_material ON products(material)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prod_maturity ON products(maturity)")

        self.conn.commit()

    def parse_xml(self, xml_file_path: str) -> Dict[str, Any]:
        """
        Parse the 3DEXPERIENCE XPDMXML file and populate SQLite database.
        
        :param xml_file_path: Path to XPDMXML file
        :return: Summary metrics of parsed items
        """
        tree = ET.parse(xml_file_path)
        root = tree.getroot()
        ns = self.XML_NAMESPACE

        # Identify root assembly reference from ProductStructure tag
        prod_struct = root.find('.//x:ProductStructure', ns)
        if prod_struct is not None:
            self.root_ref = prod_struct.attrib.get('rootRefs', 'ID_0')
        else:
            self.root_ref = 'ID_0'

        products_data = []
        for p in root.findall('.//x:Product', ns):
            xml_id = p.attrib.get('id', '')
            plm_id = (p.findtext('x:ID', default='', namespaces=ns) or '').strip()
            name = (p.findtext('x:Name', default='', namespaces=ns) or '').strip()
            description = (p.findtext('x:Description', default='', namespaces=ns) or '').strip()
            revision = (p.findtext('x:RevisionName', default='', namespaces=ns) or '').strip()
            
            rev_idx_txt = (p.findtext('x:RevisionIndex', default='', namespaces=ns) or '').strip()
            revision_index = int(rev_idx_txt) if rev_idx_txt.isdigit() else None
            
            revision_family = (p.findtext('x:RevisionFamily', default='', namespaces=ns) or '').strip()
            maturity = (p.findtext('x:Maturity', default='', namespaces=ns) or '').strip()
            lifecycle = (p.findtext('x:Lifecycle', default='', namespaces=ns) or '').strip()
            
            mass_txt = (p.findtext('x:WCGEquivalentComputedExt.V_WCG_Mass', default='', namespaces=ns) or '').strip()
            try:
                mass = float(mass_txt) if mass_txt else 0.0
            except ValueError:
                mass = 0.0

            material = (p.findtext('x:Material', default='', namespaces=ns) or '').strip()
            owner = (p.findtext('x:Owner', default='', namespaces=ns) or '').strip()
            organization = (p.findtext('x:Organization', default='', namespaces=ns) or '').strip()
            project = (p.findtext('x:Project', default='', namespaces=ns) or '').strip()
            time_created = (p.findtext('x:TimeCreated', default='', namespaces=ns) or '').strip()
            time_modified = (p.findtext('x:TimeModified', default='', namespaces=ns) or '').strip()

            products_data.append((
                xml_id, plm_id, name, description, revision, revision_index,
                revision_family, maturity, lifecycle, mass, material,
                owner, organization, project, time_created, time_modified
            ))

        instances_data = []
        for inst in root.findall('.//x:ProductInst', ns):
            inst_xml_id = inst.attrib.get('id', '')
            inst_name = (inst.findtext('x:ID', default='', namespaces=ns) or '').strip()
            parent_xml_id = (inst.findtext('x:Owned', default='', namespaces=ns) or '').strip()
            child_xml_id = (inst.findtext('x:Instancing', default='', namespaces=ns) or '').strip()
            
            order_txt = (inst.findtext('x:ChildOrder', default='', namespaces=ns) or '').strip()
            try:
                child_order = float(order_txt) if order_txt else 0.0
            except ValueError:
                child_order = 0.0

            instances_data.append((
                inst_xml_id, inst_name, parent_xml_id, child_xml_id, child_order
            ))

        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM product_instances")
        cursor.execute("DELETE FROM products")

        cursor.executemany("""
            INSERT INTO products (
                xml_id, plm_id, name, description, revision, revision_index,
                revision_family, maturity, lifecycle, mass, material,
                owner, organization, project, time_created, time_modified
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, products_data)

        cursor.executemany("""
            INSERT INTO product_instances (
                instance_xml_id, instance_name, parent_xml_id, child_xml_id, child_order
            ) VALUES (?, ?, ?, ?, ?)
        """, instances_data)

        self.conn.commit()

        return {
            "root_ref": self.root_ref,
            "total_products": len(products_data),
            "total_instances": len(instances_data)
        }

    def get_products_df(self) -> pd.DataFrame:
        """Return all products as a Pandas DataFrame."""
        return pd.read_sql_query("SELECT * FROM products", self.conn)

    def get_instances_df(self) -> pd.DataFrame:
        """Return all product instances as a Pandas DataFrame."""
        return pd.read_sql_query("SELECT * FROM product_instances", self.conn)

    def close(self):
        """Close database connection."""
        self.conn.close()


if __name__ == "__main__":
    db = PLMDatabase()
    summary = db.parse_xml("XPDMXML_ECAR_PLM.xml")
    print(f"Parsing complete: {summary}")
    
    df_products = db.get_products_df()
    df_instances = db.get_instances_df()
    
    print("\n--- Products Head ---")
    print(df_products[["xml_id", "plm_id", "name", "revision", "maturity", "mass", "material"]].head())
    
    print("\n--- Product Instances Head ---")
    print(df_instances.head())
    
    print("\n--- Summary Statistics ---")
    print(f"Total Unique Products: {len(df_products)}")
    print(f"Total Assembly Relations: {len(df_instances)}")
    print(f"Missing Materials count: {(df_products['material'] == '').sum()}")
    print(f"Total Top-level Mass: {df_products.loc[df_products['xml_id'] == summary['root_ref'], 'mass'].values[0]} kg")
