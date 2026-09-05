from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class SellerDiscoverySource(ABC):
    """
    Abstract interface for discovering products and extracting ALL associated seller offers per product.
    Allows replacing or augmenting Amazon public web discovery with official/permitted APIs
    without modifying database, normalization, or export layers.
    """
    
    @abstractmethod
    def discover_products(self, search_url: str, limit: int = 10, max_pages: int = 1) -> List[Dict[str, Any]]:
        """
        Discovers product listings from a category or search URL.
        Returns list of dicts containing: asin, product_url, title, category.
        """
        pass

    @abstractmethod
    def extract_seller_offers(self, product_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extracts ALL publicly accessible seller offers associated with a product.
        Returns list of raw seller offer dicts (containing seller_name, seller_profile_url, price, condition, etc.).
        """
        pass
