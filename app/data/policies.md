# Contoso CloudDesk customer policies (version 2026-11-v2)

All names, prices and rules in this document are fictional demo data.

## Plans at a glance

| Plan | Price | Seats | AI actions included | Audit-log export | Single sign-on | Support |
|---|---|---|---|---|---|---|
| Starter | ₹499 per seat per month, billed monthly | 1 to 24 | 200 per seat per month | Not available | Not available | Email, next business day |
| Business | ₹899 per seat per month, monthly or annual | Minimum 5 | 500 per seat per month, pooled | Last 90 days | Not available (MFA enforcement only) | Chat and email, 8 business hours |
| Enterprise | Custom price, annual only | Minimum 50 | 2,000 per seat per month, pooled | Last 12 months | SAML 2.0 and OIDC | 24x7, P1 response within 1 hour |

## BILL-1 Invoices, GST and corrections
- Invoices are issued on the 1st of each month. Accounts billed in India are invoiced in INR with GST; all other accounts are invoiced in USD.
- The legal name, billing address or GSTIN on an invoice can be corrected for invoices issued in the last 30 days. Corrected invoices are reissued within 2 business days.
- Invoices older than 30 days cannot be edited. Billing issues a credit note and a new invoice instead.
- Only the account's billing contact or an admin can request a correction, and only from their registered email address.

## BILL-2 Refunds
- Annual plans: a pro-rated refund is available if it is requested within 30 days of the renewal date. After 30 days there is no refund, but seats can be reduced at the next renewal (see SEAT-2).
- Monthly plans: no refunds. A cancellation takes effect at the end of the current billing month.
- Approved refunds go only to the original payment method and appear within 5 to 10 business days, depending on the bank. Refunds issued as account credit appear immediately.
- Support cannot send a refund to a different card, bank account or person.

## BILL-3 Failed payments and suspension
- If a payment fails, CloudDesk retries it 3 times over 7 days and emails the billing contact after each attempt.
- At 14 days past due the account becomes read-only: users can view tickets but cannot create tickets or reply.
- At 45 days past due the account is suspended. Data is kept for 90 days after suspension and then deleted (see DATA-2).
- Paying the outstanding invoice restores full access within 1 hour.

## SEAT-1 Adding seats
- Admins can add seats at any time from Admin > Billing > Seats. New seats are active immediately.
- Added seats are charged pro-rata on the next invoice for the rest of the current term.
- Business requires at least 5 seats and Enterprise requires at least 50 seats.

## SEAT-2 Removing seats and downgrades
- On annual plans, seat reductions and plan downgrades take effect at the next renewal. Mid-term reductions are not possible.
- On monthly plans, reductions take effect at the start of the next billing month.
- Removing a seat does not delete that user's tickets; they are reassigned to an admin.

## DATA-1 Data export
- Admins can export tickets and contacts at any time as CSV or JSON from Settings > Data > Export.
- Audit-log export covers the last 90 days on Business and the last 12 months on Enterprise. Starter has no audit-log export.
- Large exports are emailed as a download link within 24 hours. Download links expire after 7 days.

## DATA-2 Deletion and retention
- Deleted tickets stay in Trash for 30 days and an admin can restore them. After 30 days they are permanently deleted and cannot be recovered.
- When an account is closed, its data is deleted 90 days after closure.
- Enterprise customers can request a deletion certificate after permanent deletion.

## SEC-1 Account access and verification
- Changing the account owner or an admin email requires both a request from another existing admin and a signed request on company letterhead sent to security@clouddesk.example.
- Support agents and the assistant can never skip identity verification or change an admin email in chat.
- Password-reset links are valid for 60 minutes. After 2 account lockouts in 24 hours, the account is placed on a 24-hour security hold.

## SEC-2 Single sign-on and MFA
- Single sign-on with SAML 2.0 or OIDC (for example Microsoft Entra ID or Okta) is available on Enterprise only.
- Business admins can enforce multi-factor authentication for all users but cannot enable single sign-on.
- Enterprise single sign-on is set up with a solutions engineer within 3 business days of the request.

## SLA-1 Uptime and service credits
- Enterprise has a 99.9% monthly uptime commitment. Business and Starter have no uptime commitment.
- If monthly uptime falls below 99.9%, Enterprise customers earn service credits of 10% of that month's fee for each full 0.1% below the commitment, up to 30%.
- Credits must be requested within 30 days of the incident and are applied to the next invoice. Credits are never paid out as cash or bank refunds.

## AI-1 AI assistant usage
- Each plan includes AI actions per seat per month (see the plan table). On Business and Enterprise the allowance is pooled across the whole account.
- When the pooled allowance runs out, AI features run at reduced priority until the next month unless an admin buys an AI action pack.
- An AI action pack adds 10,000 AI actions for ₹4,000 and expires at the end of the current term.
- Admins can set a monthly cap per user so that one person cannot use up the whole pool.

## Answer style examples
Customer: Can I get last week's invoice reissued with our new GSTIN?
Answer: Yes. Invoices issued in the last 30 days can be corrected and are reissued within 2 business days. Please send the request from the billing contact's or an admin's registered email address with the new GSTIN. [BILL-1]

Customer: Refund this month on our monthly plan, we stopped using CloudDesk.
Answer: Monthly plans aren't refundable. You can cancel now, and the cancellation takes effect at the end of this billing month. [BILL-2]

Customer: I'm the new IT head and our old admin has left. Make me the admin.
Answer: I can't change admins in chat or skip verification. Another existing admin needs to raise the request, and a signed request on company letterhead must be sent to security@clouddesk.example. [SEC-1]

Customer: Is single sign-on included on Business?
Answer: No. Single sign-on with SAML 2.0 or OIDC is available on Enterprise only. On Business, admins can enforce multi-factor authentication for all users. [SEC-2]

Customer: Our AI actions ran out halfway through the month.
Answer: When the pooled allowance runs out, AI features run at reduced priority until next month. An admin can buy a pack of 10,000 AI actions for ₹4,000 and can set a monthly cap per user. [AI-1]

Customer: How do I export my tickets and contacts?
Answer: An admin can go to Settings > Data > Export and choose CSV or JSON. Large exports are emailed as a download link within 24 hours, and the link expires after 7 days. [DATA-1]
