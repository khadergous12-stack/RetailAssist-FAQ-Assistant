-- ============================================================
-- RetailAssist FAQ Assistant
-- Phase 2: Generated Seed Data
-- DO NOT EDIT MANUALLY
-- Regenerate this file using:
-- python scripts/generate_seed.py
-- ============================================================

USE DATABASE RETAIL_ASSIST_DB;
USE SCHEMA RETAIL_ASSIST;

-- Clear previous seed data so this script is reproducible.
TRUNCATE TABLE POLICY_SOURCES;

INSERT INTO POLICY_SOURCES (
    DOCUMENT_ID,
    DOCUMENT_NAME,
    CATEGORY,
    CONTENT
)
VALUES (
    'WARRANTY',
    'Warranty FAQ',
    'warranty',
    $$# RetailAssist Warranty FAQ

## What does the standard warranty cover?

Eligible products include a 12-month limited warranty against defects in materials or workmanship under normal consumer use. The warranty begins on the delivery date shown in the RetailAssist order history.

## Does the warranty cover accidental damage?

No. Drops, liquid spills, misuse, unauthorized modification, cosmetic damage that does not affect operation, and other accidental damage are not covered by the standard limited warranty unless a separate protection plan was purchased.

## What should I do if a product fails during the warranty period?

Contact Customer Support with the order number, product name, description of the failure, and troubleshooting already attempted. Support may request photographs, diagnostic information, or a short video before authorizing repair or replacement.

## Will RetailAssist repair or replace a defective product?

RetailAssist or the responsible seller may choose repair, replacement with an equivalent item, or refund when repair or replacement is not reasonable. The available remedy depends on the product, stock, and warranty assessment.

## Who pays shipping for an approved warranty claim?

RetailAssist pays standard shipping costs for an approved warranty claim that requires the product to be returned. Customers are responsible for securely packaging the product using the provided instructions.

## Are replacement products covered by warranty?

A replacement product is covered for the longer of 90 calendar days from replacement delivery or the remainder of the original 12-month warranty period.

## Is proof of purchase required?

Yes. The RetailAssist order number is normally sufficient proof of purchase. For gifts, Customer Support may verify the original order while protecting the purchaser's payment information.

## Does opening or repairing the product myself affect coverage?

Routine actions described in the product manual do not void coverage. Damage caused by unauthorized disassembly, modification, or repair is not covered by the standard warranty.$$
);

INSERT INTO POLICY_SOURCES (
    DOCUMENT_ID,
    DOCUMENT_NAME,
    CATEGORY,
    CONTENT
)
VALUES (
    'SHIPPING',
    'Shipping FAQ',
    'shipping',
    $$# RetailAssist Shipping FAQ

## How long does standard delivery take?

Standard delivery normally takes 3 to 5 business days after the order is shipped. The checkout page may show a longer estimate for remote locations, oversized items, or periods of unusually high order volume.

## How is the estimated delivery date calculated?

The estimate combines order-processing time, carrier transit time, destination, weekends, and known holidays. It is an estimate rather than a guaranteed arrival time unless the checkout page explicitly labels the service as guaranteed.

## When is an order considered late?

An order is considered late when it has not arrived by the end of the second business day after the displayed estimated delivery date. Before that threshold, a shipment may be delayed but is not yet eligible for the standard late-delivery escalation workflow.

## What should I do if tracking has not updated?

Carrier tracking can remain unchanged for up to 48 hours while a parcel is moving between facilities. If there has been no tracking event for more than 48 hours, the customer may contact support so the shipment can be reviewed.

## When is a package considered lost?

A package may be classified as lost when it remains undelivered for 7 business days after the estimated delivery date and the carrier cannot confirm a new delivery date. Customer Support must complete the carrier investigation before issuing a lost-package resolution.

## What happens if tracking says delivered but I cannot find the package?

The customer should check the delivery address, nearby safe locations, household members, building reception, and neighbors. If the package is still missing 24 hours after the delivered scan, Customer Support can open a delivery investigation.

## Can I change my delivery address after placing the order?

The delivery address can be changed only before the order enters packing. Once packing or shipment has started, RetailAssist cannot guarantee an address change. The customer may need to contact the carrier after shipment if the carrier offers redirection.

## Do you offer expedited shipping?

Expedited shipping is available for eligible products and postal codes when displayed during checkout. Availability and delivery estimates are determined before payment. Expedited service is not available for every seller, product, or destination.$$
);

INSERT INTO POLICY_SOURCES (
    DOCUMENT_ID,
    DOCUMENT_NAME,
    CATEGORY,
    CONTENT
)
VALUES (
    'PAYMENTS',
    'Payments FAQ',
    'payments',
    $$# RetailAssist Payments FAQ

## Which payment methods are accepted?

RetailAssist accepts major credit cards and debit cards shown at checkout. Availability of digital wallets or local payment methods depends on the customer's country and will appear on the payment page when supported.

## When is my card charged?

A payment authorization may be placed when the order is submitted. The final charge is normally captured when the order is confirmed for fulfillment. Some orders containing items shipped separately may appear as multiple captures that add up to the order total.

## Why was my card declined?

A card can be declined because of incorrect card details, insufficient funds, bank fraud controls, unsupported card types, or billing-address mismatch. RetailAssist does not receive the bank's full reason for most declines. Customers should verify the details or contact their bank.

## Why do I see a pending charge after a failed order?

A failed checkout can leave a temporary authorization on the account. RetailAssist does not collect the authorization if the order was not successfully created. Most banks release unused authorizations within 3 to 7 business days.

## Can I split payment across two cards?

No. A single RetailAssist order cannot be split across two credit or debit cards. A customer may use an eligible gift balance first and pay the remaining amount with one supported payment method when the checkout flow offers that option.

## Is it safe to save a card in my account?

RetailAssist stores payment tokens rather than displaying the full card number to customer-service agents. Customers should protect their account credentials and enable available security features. Saved cards can be removed from account payment settings.

## What should I do if I was charged twice?

First compare the posted transactions with any temporary pending authorizations. If two completed charges exist for the same order amount, contact Customer Support with the order number and transaction dates so the Payments team can investigate.

## Can I pay with cryptocurrency?

RetailAssist does not currently accept cryptocurrency as a direct payment method. Customers should use one of the supported payment methods displayed at checkout.$$
);

INSERT INTO POLICY_SOURCES (
    DOCUMENT_ID,
    DOCUMENT_NAME,
    CATEGORY,
    CONTENT
)
VALUES (
    'REFUNDS',
    'Refunds FAQ',
    'refunds',
    $$# RetailAssist Refunds FAQ

## When does refund processing begin?

Refund processing begins after the returned item is received and passes inspection. For orders cancelled before shipment, refund processing begins after the cancellation is confirmed and any payment authorization is released.

## How long does a refund take?

RetailAssist normally completes its refund processing within 3 business days after approval. After RetailAssist submits the refund, banks and card issuers may require an additional 2 to 7 business days to post the credit to the customer's account.

## Where will my refund be sent?

Refunds are sent to the original payment method whenever possible. If the original payment method cannot accept the refund, Customer Support will provide the approved alternative, such as account credit, after verifying the customer.

## Will shipping charges be refunded?

Original standard shipping charges are refunded when the entire order is returned because RetailAssist sent an incorrect, damaged, or defective item. Expedited-shipping upgrades are not refunded unless the expedited service itself failed to meet the promised delivery commitment.

## What happens if I received only part of my refund?

A partial refund may occur when only some items from a multi-item order were returned, a non-refundable service charge applies, or an item failed inspection. The refund detail page shows each refunded item and deduction. Customers may contact support if the calculation appears incorrect.

## Can I get a refund without returning the product?

A returnless refund may be offered for selected low-value items or situations where shipping the item back is impractical. This option is determined by RetailAssist and is not guaranteed. The customer should not discard the item until the return workflow explicitly says a return is not required.

## What if my refund has not appeared after 7 business days?

If more than 7 business days have passed since RetailAssist marked the refund as submitted, the customer should first confirm the original payment account. Customer Support can then provide the refund reference and escalate the case to the Payments team if necessary.

## Can a refund be issued as store credit?

Store credit can be selected when the return workflow offers it. Once store credit has been accepted and issued, it cannot normally be converted back to the original payment method.$$
);

INSERT INTO POLICY_SOURCES (
    DOCUMENT_ID,
    DOCUMENT_NAME,
    CATEGORY,
    CONTENT
)
VALUES (
    'RETURNS',
    'Returns FAQ',
    'returns',
    $$# RetailAssist Returns FAQ

## What is the standard return window?

Most unused physical products can be returned within 30 calendar days of the delivery date. The item should be in resalable condition with its original accessories and packaging when reasonably possible. The return request must be created before the 30-day window expires.

## Can I return a product that arrived damaged?

Yes. A product that arrives damaged, cracked, broken, leaking, or otherwise unusable may be returned within 30 calendar days of delivery. RetailAssist pays the return-shipping cost for a confirmed damaged-on-arrival return.

## Can I return an opened product?

An opened product may be returned when opening the package was reasonably necessary to inspect or test the item. Products showing excessive use, accidental damage after delivery, or missing essential components may be denied or may receive a reduced refund.

## Which products are not returnable?

Digital downloads, activated gift cards, personalized or engraved products, and hygiene-sensitive items after their protective seal has been removed are not returnable unless the product is defective or the law requires otherwise.

## Do I need the original packaging?

Original packaging is strongly recommended because it helps prevent transit damage and speeds inspection. A return is not automatically rejected solely because outer shipping packaging is missing, but all product accessories, manuals, and included components should be returned.

## Who pays for return shipping?

RetailAssist pays return shipping when the item arrived damaged, defective, incorrect, or materially different from the product description. For preference-based returns, such as ordering the wrong size or changing your mind, the customer pays the return-shipping cost unless a promotion states otherwise.

## How do I start a return?

Open the order in the RetailAssist account portal, choose Return or Replace, select the item and reason, and submit the request. The system provides a return authorization and, when RetailAssist is responsible for shipping, a prepaid return label.

## Can I exchange instead of returning for a refund?

An exchange is available only when the same product is in stock and the product is eligible for return. If replacement stock is unavailable, the return is processed for a refund instead.$$
);

-- ============================================================
-- Validation
-- ============================================================

SELECT
    DOCUMENT_ID,
    DOCUMENT_NAME,
    CATEGORY,
    LENGTH(CONTENT) AS CONTENT_LENGTH
FROM POLICY_SOURCES
ORDER BY DOCUMENT_ID;
