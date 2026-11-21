# Data source and attribution

Olist published the **Brazilian E-Commerce Public Dataset by Olist**, version 2, on Kaggle. These are anonymized historical marketplace records. The original archive contains purchases from 2016 to 2018.

- Publisher and dataset: [Olist on Kaggle](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce/versions/2)
- Data license: [Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-nc-sa/4.0/)
- Downloaded archive SHA-256: `967e41e04fc306fe604e2a693f488995a8b41e5047418f8a5c8e4abd6deca784`

The included January–March 2017 sample takes real purchase cohorts from the orders, customers, order items and payments tables. The integration project reserializes the source records into monthly CSVs. The reporting and quality projects use one row per order with amounts in Brazilian centavos, aggregated item and payment lines, customer state, and calendar dates. Quality-drill commands deliberately alter a separate output copy.

The original data, adapted datasets and data-derived outputs in these projects carry the same CC BY-NC-SA 4.0 license. Credit Olist, preserve the notice, identify your changes and follow the linked terms when redistributing them. The projects were prepared independently of Olist.
