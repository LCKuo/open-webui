BEGIN;

COMMENT ON DATABASE interact_crm_demo IS 'Interact Vision CRM demo database. Signed contracts, sales pipeline, customer activity, and employee follow-up history are stored separately.';

-- Keep reporting views at a documented, fanout-safe grain.
CREATE OR REPLACE VIEW crm.v_customer_summary AS
WITH contact_stats AS (
    SELECT company_id, COUNT(*) AS contacts_count
    FROM crm.contacts
    GROUP BY company_id
), opportunity_stats AS (
    SELECT
        company_id,
        COUNT(*) AS opportunities_count,
        COALESCE(SUM(amount_ntd) FILTER (WHERE stage NOT IN ('won', 'lost')), 0) AS open_pipeline_ntd
    FROM crm.opportunities
    GROUP BY company_id
), deal_stats AS (
    SELECT company_id, COALESCE(SUM(signed_amount_ntd), 0) AS signed_amount_ntd
    FROM crm.deals
    GROUP BY company_id
), follow_up_stats AS (
    SELECT company_id, MAX(follow_up_at) AS last_follow_up_at
    FROM crm.follow_ups
    GROUP BY company_id
)
SELECT
    company.id AS company_id,
    company.name AS company_name,
    company.industry,
    company.status,
    company.tier,
    owner.name AS owner_name,
    COALESCE(contact_stats.contacts_count, 0) AS contacts_count,
    COALESCE(opportunity_stats.opportunities_count, 0) AS opportunities_count,
    COALESCE(opportunity_stats.open_pipeline_ntd, 0) AS open_pipeline_ntd,
    COALESCE(deal_stats.signed_amount_ntd, 0) AS signed_amount_ntd,
    follow_up_stats.last_follow_up_at
FROM crm.companies company
JOIN crm.users owner ON owner.id = company.owner_user_id
LEFT JOIN contact_stats ON contact_stats.company_id = company.id
LEFT JOIN opportunity_stats ON opportunity_stats.company_id = company.id
LEFT JOIN deal_stats ON deal_stats.company_id = company.id
LEFT JOIN follow_up_stats ON follow_up_stats.company_id = company.id;

CREATE OR REPLACE VIEW crm.v_employee_activity AS
WITH follow_up_stats AS (
    SELECT user_id, COUNT(*) AS follow_up_count, MAX(follow_up_at) AS last_follow_up_at
    FROM crm.follow_ups
    GROUP BY user_id
), deal_stats AS (
    SELECT
        owner_user_id AS user_id,
        COUNT(*) AS won_deal_count,
        COALESCE(SUM(signed_amount_ntd), 0) AS signed_amount_ntd
    FROM crm.deals
    GROUP BY owner_user_id
)
SELECT
    employee.id AS user_id,
    employee.name AS employee_name,
    employee.department,
    employee.role,
    COALESCE(follow_up_stats.follow_up_count, 0) AS follow_up_count,
    COALESCE(deal_stats.won_deal_count, 0) AS won_deal_count,
    COALESCE(deal_stats.signed_amount_ntd, 0) AS signed_amount_ntd,
    follow_up_stats.last_follow_up_at
FROM crm.users employee
LEFT JOIN follow_up_stats ON follow_up_stats.user_id = employee.id
LEFT JOIN deal_stats ON deal_stats.user_id = employee.id;

COMMENT ON SCHEMA crm IS 'CRM operational and reporting schema. Base tables retain event history; reporting views declare their row grain and aggregation period in object comments.';

COMMENT ON TABLE crm.users IS 'Employee directory. Grain: one row per employee; id is the stable internal employee key.';
COMMENT ON COLUMN crm.users.id IS 'Stable internal employee identifier and primary key.';
COMMENT ON COLUMN crm.users.employee_no IS 'Unique business-facing employee number.';
COMMENT ON COLUMN crm.users.name IS 'Employee display name; not guaranteed to be a unique business key.';
COMMENT ON COLUMN crm.users.email IS 'Unique employee email address; personally identifiable information.';
COMMENT ON COLUMN crm.users.phone IS 'Employee phone number; personally identifiable information.';
COMMENT ON COLUMN crm.users.department IS 'Organizational department used for grouping and filtering employees.';
COMMENT ON COLUMN crm.users.role IS 'Employee role. Valid values are defined by crm.employee_role.';
COMMENT ON COLUMN crm.users.manager_id IS 'Direct manager employee id; self-reference to crm.users.id.';
COMMENT ON COLUMN crm.users.region IS 'Employee sales or service region.';
COMMENT ON COLUMN crm.users.active IS 'Whether the employee account is currently active.';
COMMENT ON COLUMN crm.users.hire_date IS 'Employee hire date.';
COMMENT ON COLUMN crm.users.created_at IS 'Timestamp when the employee record was created.';

COMMENT ON TABLE crm.companies IS 'CRM customer and prospect organizations. Grain: one row per company.';
COMMENT ON COLUMN crm.companies.id IS 'Stable company identifier and primary key.';
COMMENT ON COLUMN crm.companies.company_code IS 'Unique business-facing company code.';
COMMENT ON COLUMN crm.companies.name IS 'Company display name.';
COMMENT ON COLUMN crm.companies.industry IS 'Company industry classification.';
COMMENT ON COLUMN crm.companies.status IS 'Customer lifecycle status. Valid values are defined by crm.company_status.';
COMMENT ON COLUMN crm.companies.tier IS 'Customer segment. Valid values are defined by crm.company_tier.';
COMMENT ON COLUMN crm.companies.tax_id IS 'Company tax identifier; restricted business identity data.';
COMMENT ON COLUMN crm.companies.website IS 'Company public website URL.';
COMMENT ON COLUMN crm.companies.city IS 'Company city or locality.';
COMMENT ON COLUMN crm.companies.address IS 'Company street address.';
COMMENT ON COLUMN crm.companies.annual_revenue_ntd IS 'Reported annual company revenue in New Taiwan dollars; not CRM sales revenue.';
COMMENT ON COLUMN crm.companies.employee_count IS 'Reported number of employees at the customer company.';
COMMENT ON COLUMN crm.companies.owner_user_id IS 'Employee responsible for the company; references crm.users.id.';
COMMENT ON COLUMN crm.companies.created_at IS 'Timestamp when the company record was created.';
COMMENT ON COLUMN crm.companies.updated_at IS 'Timestamp when the company record was last updated.';

COMMENT ON TABLE crm.contacts IS 'People associated with CRM companies. Grain: one row per contact.';
COMMENT ON COLUMN crm.contacts.id IS 'Stable contact identifier and primary key.';
COMMENT ON COLUMN crm.contacts.company_id IS 'Company containing this contact; references crm.companies.id.';
COMMENT ON COLUMN crm.contacts.name IS 'Contact display name.';
COMMENT ON COLUMN crm.contacts.title IS 'Contact job title.';
COMMENT ON COLUMN crm.contacts.email IS 'Contact email address; personally identifiable information.';
COMMENT ON COLUMN crm.contacts.phone IS 'Contact phone number; personally identifiable information.';
COMMENT ON COLUMN crm.contacts.line_id IS 'Contact LINE identifier; personally identifiable information.';
COMMENT ON COLUMN crm.contacts.is_primary IS 'Whether this is the primary contact for the company.';
COMMENT ON COLUMN crm.contacts.created_at IS 'Timestamp when the contact record was created.';

COMMENT ON TABLE crm.products IS 'Sellable products and services. Grain: one row per product SKU.';
COMMENT ON COLUMN crm.products.id IS 'Stable product identifier and primary key.';
COMMENT ON COLUMN crm.products.sku IS 'Unique stock-keeping or service code.';
COMMENT ON COLUMN crm.products.name IS 'Product or service display name.';
COMMENT ON COLUMN crm.products.category IS 'Product category used for grouping.';
COMMENT ON COLUMN crm.products.list_price_ntd IS 'Standard list price in New Taiwan dollars; not necessarily the transacted price.';
COMMENT ON COLUMN crm.products.recurring IS 'Whether the product normally produces recurring charges.';
COMMENT ON COLUMN crm.products.active IS 'Whether the product is currently available for sale.';

COMMENT ON TABLE crm.opportunities IS 'Sales pipeline opportunities before contract signing. Grain: one row per opportunity; amounts are potential, not realized sales.';
COMMENT ON COLUMN crm.opportunities.id IS 'Stable opportunity identifier and primary key.';
COMMENT ON COLUMN crm.opportunities.company_id IS 'Prospect or customer company; references crm.companies.id.';
COMMENT ON COLUMN crm.opportunities.contact_id IS 'Primary opportunity contact; references crm.contacts.id when present.';
COMMENT ON COLUMN crm.opportunities.owner_user_id IS 'Employee responsible for the opportunity; references crm.users.id.';
COMMENT ON COLUMN crm.opportunities.name IS 'Opportunity display name.';
COMMENT ON COLUMN crm.opportunities.stage IS 'Pipeline stage. Valid values are defined by crm.opportunity_stage.';
COMMENT ON COLUMN crm.opportunities.amount_ntd IS 'Potential opportunity amount in New Taiwan dollars; do not treat as signed sales revenue.';
COMMENT ON COLUMN crm.opportunities.probability IS 'Estimated close probability from 0 through 100.';
COMMENT ON COLUMN crm.opportunities.expected_close_date IS 'Forecast date on which the opportunity may close; not an actual signed date.';
COMMENT ON COLUMN crm.opportunities.source IS 'Lead or opportunity acquisition source.';
COMMENT ON COLUMN crm.opportunities.pain_point IS 'Customer problem or need associated with the opportunity.';
COMMENT ON COLUMN crm.opportunities.next_step IS 'Planned next sales action.';
COMMENT ON COLUMN crm.opportunities.created_at IS 'Timestamp when the opportunity was created.';
COMMENT ON COLUMN crm.opportunities.updated_at IS 'Timestamp when the opportunity was last updated.';

COMMENT ON TABLE crm.opportunity_products IS 'Bridge between opportunities and products. Grain: one row per opportunity and product pair.';
COMMENT ON COLUMN crm.opportunity_products.opportunity_id IS 'Opportunity identifier; part of the composite primary key and references crm.opportunities.id.';
COMMENT ON COLUMN crm.opportunity_products.product_id IS 'Product identifier; part of the composite primary key and references crm.products.id.';
COMMENT ON COLUMN crm.opportunity_products.quantity IS 'Proposed product quantity for the opportunity.';
COMMENT ON COLUMN crm.opportunity_products.unit_price_ntd IS 'Proposed unit price in New Taiwan dollars for this opportunity line.';

COMMENT ON TABLE crm.follow_ups IS 'Historical customer follow-up events. Grain: one row per completed or recorded follow-up event.';
COMMENT ON COLUMN crm.follow_ups.id IS 'Stable follow-up event identifier and primary key; count distinct id for activity totals.';
COMMENT ON COLUMN crm.follow_ups.company_id IS 'Company involved in the follow-up; references crm.companies.id.';
COMMENT ON COLUMN crm.follow_ups.contact_id IS 'Contact involved in the follow-up; references crm.contacts.id when present.';
COMMENT ON COLUMN crm.follow_ups.opportunity_id IS 'Related opportunity; references crm.opportunities.id when present.';
COMMENT ON COLUMN crm.follow_ups.user_id IS 'Employee who performed or recorded the follow-up; references crm.users.id.';
COMMENT ON COLUMN crm.follow_ups.follow_up_type IS 'Communication or activity type. Valid values are defined by crm.follow_up_type.';
COMMENT ON COLUMN crm.follow_ups.subject IS 'Short follow-up subject.';
COMMENT ON COLUMN crm.follow_ups.content IS 'Detailed follow-up notes; may contain confidential customer information.';
COMMENT ON COLUMN crm.follow_ups.outcome IS 'Recorded result of the follow-up.';
COMMENT ON COLUMN crm.follow_ups.next_action IS 'Planned action after this follow-up.';
COMMENT ON COLUMN crm.follow_ups.follow_up_at IS 'Business event timestamp when the follow-up occurred; use for period filtering.';
COMMENT ON COLUMN crm.follow_ups.created_at IS 'System timestamp when the follow-up record was created; not necessarily the event time.';

COMMENT ON TABLE crm.deals IS 'Signed sales contracts derived from won opportunities. Grain: one row per signed opportunity and contract.';
COMMENT ON COLUMN crm.deals.id IS 'Stable signed deal identifier and primary key.';
COMMENT ON COLUMN crm.deals.opportunity_id IS 'Unique source opportunity; references crm.opportunities.id.';
COMMENT ON COLUMN crm.deals.company_id IS 'Contracted company; references crm.companies.id.';
COMMENT ON COLUMN crm.deals.owner_user_id IS 'Employee credited with the signed deal; references crm.users.id.';
COMMENT ON COLUMN crm.deals.contract_no IS 'Unique contract number.';
COMMENT ON COLUMN crm.deals.deal_name IS 'Signed deal display name.';
COMMENT ON COLUMN crm.deals.signed_amount_ntd IS 'Actual signed contract amount in New Taiwan dollars; sum this for signed sales rankings.';
COMMENT ON COLUMN crm.deals.gross_margin_percent IS 'Expected or recorded deal gross margin percentage.';
COMMENT ON COLUMN crm.deals.signed_date IS 'Date the contract was signed; use for realized sales period filtering.';
COMMENT ON COLUMN crm.deals.start_date IS 'Contract service start date.';
COMMENT ON COLUMN crm.deals.end_date IS 'Contract service end date.';
COMMENT ON COLUMN crm.deals.status IS 'Contract lifecycle status. Valid values are defined by crm.deal_status; it is not opportunity win/loss status.';
COMMENT ON COLUMN crm.deals.payment_terms IS 'Contract payment terms.';
COMMENT ON COLUMN crm.deals.created_at IS 'Timestamp when the deal record was created.';

COMMENT ON TABLE crm.sales_targets IS 'Monthly employee sales targets. Grain: one row per employee and target month.';
COMMENT ON COLUMN crm.sales_targets.id IS 'Stable sales target identifier and primary key.';
COMMENT ON COLUMN crm.sales_targets.user_id IS 'Employee assigned the target; references crm.users.id.';
COMMENT ON COLUMN crm.sales_targets.target_month IS 'First day of the target month; use as the monthly time dimension.';
COMMENT ON COLUMN crm.sales_targets.target_amount_ntd IS 'Assigned sales target amount in New Taiwan dollars.';
COMMENT ON COLUMN crm.sales_targets.created_at IS 'Timestamp when the target record was created.';

COMMENT ON VIEW crm.v_customer_summary IS 'Pre-aggregated customer summary. Grain: exactly one row per company. Counts, pipeline, signed amount, and latest follow-up are aggregated independently to prevent fanout. Metrics cover all available history; last_follow_up_at is recency, not an event-period dimension.';
COMMENT ON COLUMN crm.v_customer_summary.company_id IS 'Stable company identifier and row grain key.';
COMMENT ON COLUMN crm.v_customer_summary.company_name IS 'Company display name.';
COMMENT ON COLUMN crm.v_customer_summary.industry IS 'Company industry classification.';
COMMENT ON COLUMN crm.v_customer_summary.status IS 'Customer lifecycle status.';
COMMENT ON COLUMN crm.v_customer_summary.tier IS 'Customer segment.';
COMMENT ON COLUMN crm.v_customer_summary.owner_name IS 'Display name of the employee responsible for the company; may not be unique.';
COMMENT ON COLUMN crm.v_customer_summary.contacts_count IS 'All-time number of contacts belonging to the company.';
COMMENT ON COLUMN crm.v_customer_summary.opportunities_count IS 'All-time number of opportunities belonging to the company.';
COMMENT ON COLUMN crm.v_customer_summary.open_pipeline_ntd IS 'Current potential amount for opportunities whose stage is neither won nor lost.';
COMMENT ON COLUMN crm.v_customer_summary.signed_amount_ntd IS 'All-time signed contract amount for the company.';
COMMENT ON COLUMN crm.v_customer_summary.last_follow_up_at IS 'Most recent follow-up event timestamp for the company; not suitable for historical event-period totals.';

COMMENT ON VIEW crm.v_sales_pipeline IS 'Sales pipeline view. Grain: one row per opportunity. Contains potential and probability-weighted amounts, not signed revenue.';
COMMENT ON COLUMN crm.v_sales_pipeline.opportunity_id IS 'Stable opportunity identifier and row grain key.';
COMMENT ON COLUMN crm.v_sales_pipeline.company_name IS 'Company display name.';
COMMENT ON COLUMN crm.v_sales_pipeline.opportunity_name IS 'Opportunity display name.';
COMMENT ON COLUMN crm.v_sales_pipeline.owner_name IS 'Opportunity owner display name; may not be unique.';
COMMENT ON COLUMN crm.v_sales_pipeline.stage IS 'Current opportunity stage.';
COMMENT ON COLUMN crm.v_sales_pipeline.amount_ntd IS 'Potential opportunity amount in New Taiwan dollars.';
COMMENT ON COLUMN crm.v_sales_pipeline.probability IS 'Estimated close probability from 0 through 100.';
COMMENT ON COLUMN crm.v_sales_pipeline.weighted_amount_ntd IS 'Potential amount multiplied by probability; a forecast, not signed revenue.';
COMMENT ON COLUMN crm.v_sales_pipeline.expected_close_date IS 'Forecast opportunity close date.';
COMMENT ON COLUMN crm.v_sales_pipeline.next_step IS 'Planned next sales action.';

COMMENT ON VIEW crm.v_employee_activity IS 'Pre-aggregated employee lifetime summary. Grain: exactly one row per employee. Follow-up and deal aggregates are calculated independently to prevent fanout. Do not use for historical period rankings because it has no event-period dimension; use crm.follow_ups or crm.deals instead.';
COMMENT ON COLUMN crm.v_employee_activity.user_id IS 'Stable employee identifier and row grain key.';
COMMENT ON COLUMN crm.v_employee_activity.employee_name IS 'Employee display name; not guaranteed unique.';
COMMENT ON COLUMN crm.v_employee_activity.department IS 'Employee department.';
COMMENT ON COLUMN crm.v_employee_activity.role IS 'Employee role.';
COMMENT ON COLUMN crm.v_employee_activity.follow_up_count IS 'All-time count of follow-up events performed by the employee.';
COMMENT ON COLUMN crm.v_employee_activity.won_deal_count IS 'All-time count of signed deals credited to the employee.';
COMMENT ON COLUMN crm.v_employee_activity.signed_amount_ntd IS 'All-time signed contract amount credited to the employee.';
COMMENT ON COLUMN crm.v_employee_activity.last_follow_up_at IS 'Most recent follow-up timestamp for the employee; recency only, not a historical event-period dimension.';

COMMIT;
