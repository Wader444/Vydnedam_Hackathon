from neo4j import GraphDatabase
import sys

# Update with your local Docker credentials
URI = "bolt://localhost:7687"
AUTH = ("neo4j", "password123") # Change 'password' to your actual local password

def verify_graph_engine():
    try:
        driver = GraphDatabase.driver(URI, auth=AUTH)
        driver.verify_connectivity()
        print("[SUCCESS] Connected to Neo4j successfully.")
    except Exception as e:
        print("[ERROR] Could not connect to Neo4j.")
        print(f"Details: {e}")
        print("\n[HELP] 'Hey team, my Neo4j container isn't accepting connections on localhost:7687. Can someone check my Docker run command or port bindings?'")
        sys.exit(1)

    with driver.session() as session:
        # Check Node Count
        node_count = session.run("MATCH (n) RETURN count(n) AS count").single()["count"]
        # Check Edge (Relationship) Count
        edge_count = session.run("MATCH ()-[r]->() RETURN count(r) AS count").single()["count"]

        if node_count == 0:
            print("[WARNING] Connected to Neo4j, but the database is EMPTY.")
            print("\n[HELP] 'Hey team, my Tree-sitter parser isn't inserting data. Can we pair-program on the Cypher MERGE query to figure out why nodes are dropping?'")
        else:
            print(f"[SUCCESS] Graph populated! Found {node_count} nodes and {edge_count} relationships.")
            
            # Show a sample to verify schema
            print("\n[INFO] Schema Sample:")
            sample = session.run("MATCH (n)-[r]->(m) RETURN labels(n)[0] AS source, type(r) AS rel, labels(m)[0] AS target LIMIT 1").single()
            if sample:
                print(f"   ({sample['source']}) -[{sample['rel']}]-> ({sample['target']})")
            else:
                print("   [WARNING] Nodes exist, but NO RELATIONSHIPS (edges) were found. Your graph is disconnected!")

    driver.close()

if __name__ == "__main__":
    verify_graph_engine()