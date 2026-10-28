# Accounting, in plain language

## Money moving is not always income or expense

If INR 10,000 salary enters the bank, the journal debits Bank and credits Salary income.

If INR 1,000 is sent from the bank to a mutual fund, Bank decreases and Investments increases. It does not become an expense just because cash left the account.

If INR 1,000 EMI consists of INR 800 principal and INR 200 interest, use a split: debit the Loan liability by INR 800, debit Interest expense by INR 200, and credit Bank by INR 1,000. The original statement row remains intact.

Card settlement reduces the card liability. Import or otherwise account for the underlying purchases and opening debt separately; treating both the card purchases and settlement as expenses would double-count spending.

## The three views

1. **Income and expenditure** shows the selected period's income, expenses and difference.
2. **Balance sheet** shows recorded assets, liabilities and equity through the end date. An initial date filter does not throw away earlier balances.
3. **Verification** checks journal equality and the assets-minus-liabilities-minus-equity difference. Statement reconciliation separately checks cash movements against source balances.

Opening net assets contribute opening equity. Recorded income/expenses contribute accumulated surplus or deficit. Equity is not changed merely to hide a difference.

## Why opening positions matter

Suppose you start with Bank INR 10,000 and a Loan INR 5,000. Opening net assets are INR 5,000. After the EMI above, Bank is INR 9,000 and the Loan is INR 4,200. Net assets are INR 4,800, and the INR 200 interest explains the reduction.

If the initial INR 5,000 loan is never entered, the app cannot know it exists. This is why the result is **recorded net worth**, not a claim to know your full wealth.

Opening positions belong to an account label for filtering. Do not enter the same real asset under multiple account labels. Saving the same account/category replaces that opening instead of adding a second one.

## Transfers and brokers

Own-account movements use a clearing account. Once both sides are imported and correctly categorised, they should cancel in the combined view. A lone side or an account-only view can leave clearing visible. Investigate it; do not force it to zero.

Broker deposits increase recorded broker cash; payouts reduce it. A negative broker position is presented as a payable. This cash position does not include unreported holdings, trades, fees or mark-to-market changes.

Investment redemption can include returned principal and a gain. Use the source investment/trade evidence to split those amounts. Goldilocks cannot calculate a reliable gain from the bank receipt alone.

## Reconciliation status

- **Balances match:** a provided or explicit statement opening, parsed movements and closing balance agree; running balances also agree.
- **Opening needs confirmation:** the opening was inferred from the first transaction, so the rows are internally consistent but not independently anchored.
- **Balance gap:** the closing equation or one or more running balances disagree.
- **More information needed:** required source balances are missing.

A matched statement can still have pending review entries. Source reconciliation includes parsed rows; accounting reports include only approved rows. These should not be confused.

Repeated/overlapping statements retain source rows so each statement can be reconciled, but matching overlap rows are not staged a second time. Where two files contain indistinguishable same-day payments without unique bank references, manual investigation may be needed.

This prototype is a cash-statement accounting aid, not an IFRS certification, independent audit, wealth valuation or tax return. Completeness and economic classification require source documents and human judgement.
