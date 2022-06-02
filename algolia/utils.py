from decimal import Decimal
from threading import Lock
from typing import List

import graphene
from django.contrib.auth.models import Permission
from django.contrib.sites.models import Site
from django.http import HttpRequest
from django.utils import timezone
from django.utils.functional import SimpleLazyObject
from gql import gql

from saleor.account.models import User
from saleor.attribute.models import Attribute, AttributeTranslation
from saleor.core.permissions import ProductPermissions
from saleor.discount.utils import fetch_discounts
from saleor.graphql.core.utils import from_global_id_or_error
from saleor.graphql.product.schema import ProductQueries
from saleor.plugins.manager import get_plugins_manager
from vendor.models import Vendor
from saleor.product.models import Category, CategoryTranslation, Product


class UserAdminContext(HttpRequest):
    def __init__(self):
        super().__init__()
        self.app = None
        self.request_time = timezone.now()
        self.site = Site.objects.get_current()
        self.plugins = SimpleLazyObject(lambda: get_plugins_manager())
        self.user, _ = User.objects.get_or_create(
            is_staff=True, is_active=True, email="manage@products.com"
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename=ProductPermissions.MANAGE_PRODUCTS.codename)
        )
        self.discounts = SimpleLazyObject(lambda: fetch_discounts(self.request_time))
        self.META = {
            "header": "http",
            "SERVER_PORT": "8000",
            "SERVER_NAME": "localhost",
        }


GET_PRODUCT_QUERY = gql(
    """
query GET_PRODUCTS($id: ID!, $languageCode: LanguageCodeEnum!) {
  products(first: 1, filter: { ids: [$id] }) {
    edges {
      node {
        name
        slug
        description
        media {
          url
        }
        thumbnail {
          url
        }
        channelListings {
          pricing {
            priceRange {
              start {
                gross {
                  amount
                  currency
                }
              }
            }
            discount {
              currency
            }
          }
          discountedPrice {
            amount
          }
          channel {
            slug
          }
          isPublished
          publicationDate
          isAvailableForPurchase
        }
        variants {
          channelListings {
            price {
              amount
            }
          }
          attributes {
            attribute {
              name
            }
            values {
              name
            }
          }
        }
        attributes {
          attribute {
            name
            translation(languageCode: $languageCode) {
              name
            }
          }
          values {
            name
            translation(languageCode: $languageCode) {
              name
            }
          }
        }
      }
    }
  }
}
"""
)


def get_categories_list_from_product(product: Product, language_code):
    if product.category:
        categories = product.category.get_ancestors(include_self=True)
        if language_code == "en":
            categories = [str(category) for category in categories]
        else:
            categories = [
                str(category.translations.filter(language_code=language_code).first())
                for category in categories
                if category.translations.filter(language_code=language_code).first()
            ]
        return categories


def get_hierarchical_categories(objects, language_code: str):
    hierarchical = {}
    hierarchical_list = []
    if hasattr(objects, "category"):
        objects = get_categories_list_from_product(objects, language_code)
    for index, category in enumerate(objects):
        hierarchical_list.append(str(category))
        hierarchical.update(
            {
                "lvl{0}".format(str(index)): " > ".join(hierarchical_list[: index + 1])
                if index != 0
                else hierarchical_list[index]
            }
        )
    return hierarchical


def map_product_description(description: dict):
    if description:
        return description.get("blocks", [{}])[0].get("data", {}).get("text", {})
    return {}


def map_product_attributes(product_dict: dict, language_code: str):
    attributes = product_dict.get("attributes", [])
    for variant in product_dict.get("variants", []):
        attributes.extend(variant.get("attributes", []))

    attrs = []
    attrs_ar = []
    if attributes:
        for attribute in attributes:
            attr_dict = {}
            attr_dict_ar = {}
            attr_dict.update(
                {
                    f"{attribute.get('attribute').get('name')}": [
                        value.get("name") for value in attribute.get("values")
                    ]
                    if attribute.get("values")
                    else []
                }
            )
            attribute_key = attribute.get("attribute").get("translation")
            if attribute_key:
                attr_dict_ar.update(
                    {
                        attribute_key.get("name"): [
                            value.get("translation").get("name")
                            for value in attribute.get("values")
                            if value.get("translation")
                        ]
                        if attribute.get("values")
                        else []
                    }
                )
            attrs.append(attr_dict)
            attrs_ar.append(attr_dict_ar)
        return attrs if language_code == "EN" else attrs_ar


def map_product_media_or_thumbnail(media: list):
    return [url.get("url") for url in media if url.get("url")]


def map_product_collections(product: Product, language_code: str):
    collections = product.collections.all()
    if not collections:
        return []
    elif collections and language_code == "EN":
        return [collection.slug for collection in collections]
    else:
        collection_translation = []
        for c in collections:
            translations = c.translations.filter(language_code=language_code.lower())
            for translation in translations:
                collection_translation.append(translation.name)
        return collection_translation


def get_product_data(product_pk: int, language_code="EN"):
    product = Product.objects.get(pk=product_pk)
    schema = graphene.Schema(query=ProductQueries, types=[Product])
    product_global_id = graphene.Node.to_global_id("Product", product_pk)
    variables = {"id": product_global_id, "languageCode": language_code.upper()}

    product_data = schema.execute(
        GET_PRODUCT_QUERY, variables=variables, context=UserAdminContext()
    )
    product_dict = product_data.data["products"]["edges"][0]["node"]

    translated_product = product.translations.filter(
        language_code=language_code.lower()
    ).first()

    description = {}
    product_name = ""
    if language_code == "EN":
        product_name = product.name
        description = product.description if product.description else {}
    elif translated_product and language_code != "EN":
        product_name = translated_product.name
        description = translated_product.description

    description = map_product_description(
        description=description,
    )

    attributes = map_product_attributes(
        product_dict=product_dict, language_code=language_code
    )

    price = {"amount": 0}
    variants_data = product_dict.pop("variants", None)
    if variants_data:
        channel_listings = variants_data[0].get("channelListings", [])
        if channel_listings:
            price = channel_listings[0].get("price", {"amount": 0})
            if price:
                price = {"amount": price.get("amount", 0)}

    channels = []
    channel_listings = product_dict.pop("channelListings", [])
    for channel in channel_listings:
        pricing = channel.pop("pricing", {})
        discounted_price = channel.pop("discountedPrice", None)
        if pricing:
            gross_price = (
                pricing.pop("priceRange", {}).pop("start", {}).pop("gross", {})
            )
            is_published = channel.pop("isPublished", False)
            is_available_for_purchase = channel.pop("isAvailableForPurchase", False)

            if is_available_for_purchase and is_published:
                name = channel.pop("channel").get("slug")
                publication_date = channel.pop("publicationDate", "")
                channel[name] = {
                    "name": name,
                    "publication_date": publication_date,
                    "price": Decimal(price.get("amount", 0)),
                    "currency": gross_price.pop("currency", 0),
                    "discounted_price": Decimal(
                        discounted_price.get("amount", 0)
                        if discounted_price and pricing.get("discount")
                        else price.get("amount", 0)
                    ),
                }
                channels.append(channel)

    skus = []
    for variant in product.variants.all():
        skus.append(variant.sku)

    vendors = []
    vendor_id = product.get_value_from_metadata("vendor")
    if vendor_id:
        _, _id = from_global_id_or_error(vendor_id, "Vendor")
        vendor = Vendor.objects.get(pk=_id)
        vendor.products.add(product)
        if vendor.brand_name not in vendors:
            vendors.append(vendor.brand_name)
    else:
        vendors = []
        product.vendor_set.clear()

    for vendor in product.vendor_set.all():
        if vendor.brand_name not in vendors:
            vendors.append(vendor.brand_name)

    celebrities = []
    for celebrity in product.celebrity_set.all():
        celebrities.append(str(celebrity))

    if not product_data.errors and channels:
        slug = product_dict.pop("slug")
        media = product_dict.pop("media", [])[:2]
        thumbnail = product_dict.pop("thumbnail", "")
        product_dict.update(
            {
                "skus": skus,
                "objectID": slug,
                "vendors": vendors,
                "channels": channels,
                "name": product_name,
                "attributes": attributes,
                "celebrities": celebrities,
                "description": description,
                "gender": product.get_value_from_metadata("gender"),
                "images": map_product_media_or_thumbnail(media=media),
                "popularity": product.get_value_from_metadata("popularity", 0),
                "thumbnail": map_product_media_or_thumbnail(media=[thumbnail])[0]
                if thumbnail
                else "",
                "collections": map_product_collections(
                    product=product, language_code=language_code
                ),
                "categories": get_hierarchical_categories(
                    objects=product, language_code=language_code.lower()
                ),
                # Mark gender and celebrities as tags in algolia index
                "_tags": {
                    "celebrities": celebrities,
                    "gender": product.get_value_from_metadata("gender"),
                },
            }
        )
        return product_dict


class SingletonMeta(type):
    _instances = {}
    _lock: Lock = Lock()

    def __call__(cls, *args, **kwargs):
        with cls._lock:
            if cls not in cls._instances:
                instance = super().__call__(*args, **kwargs)
                cls._instances[cls] = instance
        return cls._instances[cls]


def get_attributes_for_faceting(locale: str) -> List:
    faceting = {
        "categories_en": Category.objects.all(),
        "attributes_en": Attribute.objects.values("name"),
        "categories_ar": CategoryTranslation.objects.filter(
            language_code=locale
        ).values("name"),
        "attributes_ar": AttributeTranslation.objects.filter(
            language_code=locale
        ).values("name"),
    }

    categories = get_hierarchical_categories(faceting[f"categories_{locale}"], locale)
    attributes_for_faceting = [
                                  f"searchable(attributes.{attribute['name']})"
                                  for attribute in faceting[f"attributes_{locale}"]
                              ] + [f"searchable(categories.{category})" for category in categories]

    return attributes_for_faceting + [
        "searchable(gender)",
        "searchable(vendors)",
        "searchable(celebrities)",
    ]
