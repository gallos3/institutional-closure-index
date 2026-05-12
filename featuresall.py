# featuresall.py
# Computes institutional metrics per Authority-Company pair (edges) in batches and exports to NDJSON (jsonl).
# Metrics:
#   - awards_pair (count), value_pair (sum of contractValue)
#   - HHI_count, HHI_value (per authority, scope-aware)
#   - PA = degA * degC (degA: distinct suppliers per authority, degC: distinct authorities per company)
#   - AA = sum(1/log(deg(ca)+1)) over awards ca between the pair (deg(ca)=#neighbors of ContractAward)
#   - HF = count(distinct awardYear) for the pair
#   - domestic = country-prefix(a.nuts3)==country-prefix(c.nuts3)
#
# Scope filters:
#   - a.nuts3 IS NOT NULL
#   - c.nuts3 IS NOT NULL
#   - c.isConsortium = false
#   - optional cpv (5-digit prefix: substring(ca.mainCPV,0,5))
#   - optional base_year (toInteger(ca.awardYear) <= base_year)

import os
import sys
import json
import math
import time
import argparse
import logging
from typing import Dict, Tuple, Optional

from neo4j import GraphDatabase


# ----------------------------
# Configuration (defaults)
# ----------------------------
DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = os.getenv("NEO4J_PASSWORD")

# Neo4j fetch size for streaming
FETCH_SIZE = 5000


# ----------------------------
# Neo4j helpers
# ----------------------------
def get_driver(uri: str, user: str, password: str):
    return GraphDatabase.driver(uri, auth=(user, password))


def cypher_scope_where() -> str:
    """
    Common WHERE scope.
    Note: we use (n:Notice)-[:publishedBy]-(a:Authority) etc as per your graph.
    """
    return """
    WHERE a.nuts3 IS NOT NULL
      AND c.nuts3 IS NOT NULL
      AND coalesce(c.isConsortium,false) = false
      AND ($cpv IS NULL OR (ca.mainCPV IS NOT NULL AND substring(ca.mainCPV,0,5) = $cpv))
      AND ($base_year IS NULL OR (ca.awardYear IS NOT NULL AND toInteger(ca.awardYear) <= $base_year))
    """


# ----------------------------
# Precompute maps (authority HHI, degrees)
# ----------------------------
def build_authority_hhi_maps(session, cpv: Optional[str], base_year: Optional[int]) -> Tuple[Dict[int, float], Dict[int, float]]:
    """
    Returns:
      hhi_count_by_aid[aid] = sum( (count_pair/total_count)^2 ) across suppliers
      hhi_value_by_aid[aid] = sum( (value_pair/total_value)^2 ) across suppliers (total_value>0 else 0)
    """
    q = f"""
    MATCH (n:Notice)-[:publishedBy]-(a:Authority)
    MATCH (n)-[:hasLot]-(l:Lot)
    MATCH (ca:ContractAward)-[:belongsToLot]-(l)
    MATCH (ca)-[:awardedTo]-(c:Company)
    {cypher_scope_where()}
    WITH id(a) AS aid, id(c) AS cid,
         count(ca) AS cnt_pair,
         sum(toFloat(ca.contractValue)) AS val_pair
    WITH aid,
         collect({{cnt: toFloat(cnt_pair), val: toFloat(val_pair)}}) AS pairs,
         sum(toFloat(cnt_pair)) AS total_cnt,
         sum(toFloat(val_pair)) AS total_val
    WITH aid,
         reduce(h=0.0, p IN pairs |
           h + CASE WHEN total_cnt = 0.0 THEN 0.0 ELSE (p.cnt/total_cnt)*(p.cnt/total_cnt) END
         ) AS hhi_count,
         reduce(h=0.0, p IN pairs |
           h + CASE WHEN total_val = 0.0 THEN 0.0 ELSE (p.val/total_val)*(p.val/total_val) END
         ) AS hhi_value
    RETURN aid, hhi_count, hhi_value
    """

    hhi_count: Dict[int, float] = {}
    hhi_value: Dict[int, float] = {}

    result = session.run(q, cpv=cpv, base_year=base_year)
    for row in result:
        aid = int(row["aid"])
        hhi_count[aid] = float(row["hhi_count"] or 0.0)
        hhi_value[aid] = float(row["hhi_value"] or 0.0)

    return hhi_count, hhi_value


def build_authority_degree_map(session, cpv: Optional[str], base_year: Optional[int]) -> Dict[int, int]:
    """
    degA[aid] = number of distinct suppliers (companies) per authority within scope
    """
    q = f"""
    MATCH (n:Notice)-[:publishedBy]-(a:Authority)
    MATCH (n)-[:hasLot]-(l:Lot)
    MATCH (ca:ContractAward)-[:belongsToLot]-(l)
    MATCH (ca)-[:awardedTo]-(c:Company)
    {cypher_scope_where()}
    WITH id(a) AS aid, id(c) AS cid
    RETURN aid, count(DISTINCT cid) AS degA
    """

    degA: Dict[int, int] = {}
    result = session.run(q, cpv=cpv, base_year=base_year)
    for row in result:
        degA[int(row["aid"])] = int(row["degA"] or 0)
    return degA


def build_company_degree_map(session, cpv: Optional[str], base_year: Optional[int]) -> Dict[int, int]:
    """
    degC[cid] = number of distinct authorities per company within scope
    """
    q = f"""
    MATCH (n:Notice)-[:publishedBy]-(a:Authority)
    MATCH (n)-[:hasLot]-(l:Lot)
    MATCH (ca:ContractAward)-[:belongsToLot]-(l)
    MATCH (ca)-[:awardedTo]-(c:Company)
    {cypher_scope_where()}
    WITH id(a) AS aid, id(c) AS cid
    RETURN cid, count(DISTINCT aid) AS degC
    """

    degC: Dict[int, int] = {}
    result = session.run(q, cpv=cpv, base_year=base_year)
    for row in result:
        degC[int(row["cid"])] = int(row["degC"] or 0)
    return degC


# ----------------------------
# Stream edges with keyset pagination
# ----------------------------
EDGE_BATCH_QUERY = f"""
// Keyset pagination over (aid, cid)
MATCH (n:Notice)-[:publishedBy]-(a:Authority)
MATCH (n)-[:hasLot]-(l:Lot)
MATCH (ca:ContractAward)-[:belongsToLot]-(l)
MATCH (ca)-[:awardedTo]-(c:Company)
{cypher_scope_where()}
WITH id(a) AS aid, id(c) AS cid,
     a.legalName AS authority_name,
     c.legalName AS company_name,
     a.nuts3 AS a_nuts3,
     c.nuts3 AS c_nuts3,
     collect(ca) AS cas
WITH aid, cid, authority_name, company_name, a_nuts3, c_nuts3,
     size(cas) AS awards_pair,
     reduce(s=0.0, x IN cas | s + coalesce(toFloat(x.contractValue),0.0)) AS value_pair,
     // HF = distinct award years
     size(apoc.coll.toSet([x IN cas WHERE x.awardYear IS NOT NULL | toInteger(x.awardYear)])) AS HF,
     // AA = sum over awards between the pair of 1/log(deg(ca)+1)
     reduce(aa=0.0, x IN cas |
        aa + (CASE
                WHEN size([(x)--() | 1]) < 1 THEN 0.0
                ELSE 1.0 / log(toFloat(size([(x)--() | 1])) + 1.0)
              END)
     ) AS AA
WHERE ($last_aid IS NULL OR aid > $last_aid OR (aid = $last_aid AND cid > $last_cid))
RETURN aid, cid, authority_name, company_name, a_nuts3, c_nuts3,
       awards_pair, value_pair, HF, AA
ORDER BY aid ASC, cid ASC
LIMIT $limit
"""



def stream_edges_and_write_json(
    session,
    out_path: str,
    cpv: Optional[str],
    base_year: Optional[int],
    hhi_count: Dict[int, float],
    hhi_value: Dict[int, float],
    degA: Dict[int, int],
    degC: Dict[int, int],
    limit_total: Optional[int],
    page_size: int,
    logger: logging.Logger,
):
    """
    Streams authority-company pairs and writes NDJSON incrementally.
    Requires APOC for apoc.coll.toSet used in HF.
    """

    last_aid = None
    last_cid = None
    written = 0

    with open(out_path, "w", encoding="utf-8") as f:
        while True:
            if limit_total is not None and written >= limit_total:
                logger.info("Reached limit_total=%s; stopping.", limit_total)
                break

            batch_limit = page_size
            if limit_total is not None:
                batch_limit = min(batch_limit, limit_total - written)

            res = session.run(
                EDGE_BATCH_QUERY,
                cpv=cpv,
                base_year=base_year,
                last_aid=last_aid,
                last_cid=last_cid,
                limit=batch_limit,
            )

            rows = list(res)
            if not rows:
                logger.info("No more rows; finished.")
                break

            for row in rows:
                aid = int(row["aid"])
                cid = int(row["cid"])

                a_nuts3 = row["a_nuts3"]
                c_nuts3 = row["c_nuts3"]
                domestic = False
                try:
                    domestic = (a_nuts3 is not None and c_nuts3 is not None and str(a_nuts3)[:2] == str(c_nuts3)[:2])
                except Exception:
                    domestic = False

                pa = float(degA.get(aid, 0) * degC.get(cid, 0))

                record = {
                    "aid": aid,
                    "cid": cid,
                    "authority": row["authority_name"],
                    "company": row["company_name"],
                    "a_nuts3": a_nuts3,
                    "c_nuts3": c_nuts3,
                    "domestic": domestic,

                    # Pair weights
                    "awards_pair": int(row["awards_pair"] or 0),
                    "value_pair": float(row["value_pair"] or 0.0),

                    # Authority-level concentration (scope-aware)
                    "HHI_count": float(hhi_count.get(aid, 0.0)),
                    "HHI_value": float(hhi_value.get(aid, 0.0)),

                    # Structural predictors
                    "PA": pa,
                    "AA": float(row["AA"] or 0.0),
                    "HF": int(row["HF"] or 0),

                    # degrees (useful for debugging / sanity checks)
                    "degA": int(degA.get(aid, 0)),
                    "degC": int(degC.get(cid, 0)),
                }

                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1

                last_aid = aid
                last_cid = cid

            logger.info("Wrote %s records so far. last=(%s,%s)", written, last_aid, last_cid)

    return written


# ----------------------------
# Main
# ----------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", default=DEFAULT_URI)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)

    parser.add_argument("--cpv", default=None, help="5-digit CPV prefix, e.g. '33600'. If omitted, all CPVs.")
    parser.add_argument("--base_year", type=int, default=None, help="Include awards with awardYear <= base_year.")

    parser.add_argument("--out", default="institutional_metrics.jsonl", help="Output NDJSON path.")
    parser.add_argument("--page_size", type=int, default=2000, help="Keyset page size (rows per batch).")
    parser.add_argument("--limit_total", type=int, default=None, help="Optional hard cap on number of edges to export.")

    parser.add_argument("--log", default="institutional_metrics.log")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler(args.log, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )
    logger = logging.getLogger("institutional_metrics")

    logger.info("Connecting to Neo4j: %s", args.uri)
    driver = get_driver(args.uri, args.user, args.password)

    try:
        with driver.session(fetch_size=FETCH_SIZE) as session:
            # Quick APOC check (HF uses apoc.coll.toSet)
            try:
                session.run("RETURN apoc.version() AS v").single()
                logger.info("APOC available.")
            except Exception as e:
                logger.error("APOC not available, but HF uses apoc.coll.toSet. Install/enable APOC or tell me to rewrite HF without APOC.")
                raise

            logger.info("Scope: cpv=%s base_year=%s", args.cpv, args.base_year)

            # 1) Precompute authority HHI maps
            t0 = time.time()
            logger.info("Building authority HHI maps...")
            hhi_count, hhi_value = build_authority_hhi_maps(session, args.cpv, args.base_year)
            logger.info("Authority HHI maps: %d authorities. (%.1fs)", len(hhi_count), time.time() - t0)

            # 2) Precompute degrees
            t0 = time.time()
            logger.info("Building degA (authority degrees)...")
            degA = build_authority_degree_map(session, args.cpv, args.base_year)
            logger.info("degA: %d authorities. (%.1fs)", len(degA), time.time() - t0)

            t0 = time.time()
            logger.info("Building degC (company degrees)...")
            degC = build_company_degree_map(session, args.cpv, args.base_year)
            logger.info("degC: %d companies. (%.1fs)", len(degC), time.time() - t0)

            # 3) Stream edges and write NDJSON
            t0 = time.time()
            logger.info("Streaming edges and writing NDJSON → %s", os.path.abspath(args.out))
            n = stream_edges_and_write_json(
                session=session,
                out_path=args.out,
                cpv=args.cpv,
                base_year=args.base_year,
                hhi_count=hhi_count,
                hhi_value=hhi_value,
                degA=degA,
                degC=degC,
                limit_total=args.limit_total,
                page_size=args.page_size,
                logger=logger,
            )
            logger.info("DONE. Wrote %d rows. (%.1fs)", n, time.time() - t0)

    finally:
        driver.close()


if __name__ == "__main__":
    main()
