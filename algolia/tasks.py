from celery.utils.log import get_task_logger

from saleor.celeryconf import app
from saleor.plugins.manager import get_plugins_manager
from saleor.product.models import Product

task_logger = get_task_logger(__name__)


@app.task
def index_products_data_into_algolia_task():
    """Update products data into Algolia index."""
    manager = get_plugins_manager()
    algolia_plugin = manager.get_plugin(plugin_id="algolia")
    if algolia_plugin and algolia_plugin.active:
        task_logger.info("Updating products data into Algolia")
        for product in Product.objects.all():
            algolia_plugin.product_updated(product, None)

    task_logger.info("Successfully updated products data into Algolia")
