import os
import requests
from datetime import datetime, timedelta

class Zone:

    def __init__(self, cf_api_key: str, api_key_email: str, zone_id: str):
        if not cf_api_key or not api_key_email or not zone_id:
            raise ValueError("Cloudflare API Key, Email and Zone ID is required")

        self.cf_api_key = cf_api_key
        self.api_key_email = api_key_email
        self.zone_id = zone_id
        self.cf_graphql_url = "https://api.cloudflare.com/client/v4/graphql"
        self.cf_api_url = "https://api.cloudflare.com/client/v4"
        self.headers = {
            "Authorization": f"Bearer {self.cf_api_key}",
            "X-AUTH-EMAIL": self.api_key_email
        }

    def get_dns_records(self):
        """
            Fetch DNS records for a given zone (A and CNAME records)
        """
        url = f"{self.cf_api_url}/zones/{self.zone_id}/dns_records"
        records = []

        for record_type in ["A", "CNAME"]:
            response = requests.get(url, headers=self.headers, params={"type": record_type})
            if response.status_code ==200:
                records.extend(response.json().get("result", []))
            else:
                print(f"Failed to fetch {record_type} records: {response.text}")

        return records

    def get_domain_plan(self):
        """
        Get the Domain Liecense or Pricing Plan so it will give a clue about which metric
        that we could actually scrape
        The License most likely FREE/PRO/BUSINESS/ENTERPRISE
        at this moment we only support FREE and BUSINESS
        """
    
        url = f"{self.cf_api_url}/zones/{self.zone_id}"
        response = requests.get(url, headers=self.headers)
        
        if response.status_code == 200:
            zone_info = response.json().get("result", {})
            plan_name = zone_info.get("plan", {}).get("name", "Unknown")
            return plan_name
        else:
            print(f"Failed to fetch plan details for zone {self.zone_id}:", response.text)
            return "Unknown"

    def get_analytics(self, start_date=(datetime.now()-timedelta(seconds=2764800)).strftime("%Y-%m-%dT%H:%M:%SZ"), end_date=datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")):

        dns_records = [result['name'] for result in self.get_dns_records()]
        listofDomainFilter = []
        queryBody = {}

        if "Business" in self.get_domain_plan():
            listofDomainFilter = [{"clientRequestHTTPHost": item} for item in dns_records]

            queryBody = {
                "query": """
                    query VisitsDaily($zoneTag: string, $filter: ZoneHttpRequestsAdaptiveGroupsFilter_InputObject){
                        viewer{
                            zones(filter: {zoneTag: $zoneTag}) {
                            series: httpRequestsAdaptiveGroups(limit: 10000, filter: $filter) {
                                count # pageview
                                avg {
                                    sampleInterval
                                    }
                                sum {
                                    edgeResponseBytes # data transfer
                                    visits # visit
                                    }
                                dimensions {
                                    metric: clientRequestHTTPHost
                                    ts: date # datetimeFifteenMinutes
                                    }
                                }
                            }
                        }
                    }
                        """,
                "variables":{
                    "zoneTag": self.zone_id,
                    "filter": {
                        "AND": [{
                            "datetime_geq": start_date,
                            "datetime_leq": end_date
                        }, {
                            "requestSource": "eyeball"
                        }, {
                            "AND": [{
                                "edgeResponseStatus": 200,
                                "edgeResponseContentTypeName": "html"
                            }]
                        }, {
                            "OR": listofDomainFilter
                        }]
                    }
                }
            }

        getDataOK = requests.post(self.cf_graphql_url, headers=self.headers, json=queryBody)
        if getDataOK.status_code != 200:
            return "ERROR"
        
        dataResult = getDataOK.json()
        if dataResult["data"] == None:
            return "KOSONG"

        dataCompiled = {"by_date":{}, "by_domain": {}}

        try:
            dataMetric = getDataOK.json()["data"]["viewer"]["zones"][0]["series"]
            for item in dataMetric:
                default = {
                    "page_views": 0,
                    "requests": 0,
                    "data_transfer_bytes": 0,
                    "error_count": 0
                }            
                ts = item["dimensions"]["ts"]
                domainName = item["dimensions"]["metric"]
                if ts not in dataCompiled["by_date"]:
                    dataCompiled["by_date"][ts] = {}
                if domainName not in dataCompiled["by_date"][ts]:
                    dataCompiled["by_date"][ts][domainName] = default
    
                dataCompiled["by_date"][ts][domainName]["page_views"] = item["count"]
                dataCompiled["by_date"][ts][domainName]["requests"] = item["sum"]["visits"]
                dataCompiled["by_date"][ts][domainName]["data_transfer_bytes"] = item["sum"]["edgeResponseBytes"]
                
                if domainName not in dataCompiled["by_domain"]:
                    dataCompiled["by_domain"][domainName] = {}
                if ts not in dataCompiled["by_domain"][domainName]:
                    dataCompiled["by_domain"][domainName][ts] = default
    
                dataCompiled["by_domain"][domainName][ts]["page_views"] = item["count"]
                dataCompiled["by_domain"][domainName][ts]["requests"] = item["sum"]["visits"]
                dataCompiled["by_domain"][domainName][ts]["data_transfer_bytes"] = item["sum"]["edgeResponseBytes"]
            queryBody["variables"]["filter"]["AND"][2]["AND"][0]["edgeResponseStatus"] = 500
            getDataFailure = requests.post(self.cf_graphql_url, headers=self.headers, json=queryBody)

            if getDataFailure.status_code != 200:
                return "ERROR to GET DATA FAILURE TRAFFIC"

            dataError = getDataFailure.json()["data"]["viewer"]["zones"][0]["series"]
            for itemError in dataError:
                ts = itemError["dimensions"]["ts"]
                domainName = itemError["dimensions"]["metric"]
                dataCompiled["by_date"][ts][domainName]["error_count"] = itemError["count"]
                dataCompiled["by_domain"][domainName][ts]["error_count"] = itemError["count"]

        except (KeyError, IndexError, TypeError):
            return "Data is MIssing or Invalid"

        return dataCompiled

        
