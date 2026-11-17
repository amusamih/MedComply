# Architecture

The framework links the evidence used to assess a consignment with the permits, shipment states and quantity commitments recorded for that consignment.

## Recording and quantity accounting

The Registration smart contract records authorities, operational roles, jurisdictions and licensing status. The Compliance smart contract manages shipment-bound permit requests and decisions. The Shipping smart contract enforces shipment transitions and national/global quantity limits.

Shipping approval reserves the exporting and importing allowances in one transaction. If either side lacks capacity, neither reservation is committed. Pickup converts the export reservation into usage; import clearance converts the import reservation into usage. Cancellation before pickup releases outstanding reservations. An approved replacement for an invalid import permit during transit retains the shipment terms, consumed export quantity and outstanding import reservation.

Supporting documents are stored through IPFS. Their content identifiers connect the documents to participant, permit, shipment and quota records. PostgreSQL holds application records and a quota cache that can be checked against smart contract state and event history.

## Assessment workflows

National verification supports import and export permit assessment. Export verification also examines the linked import authorization. Global verification combines both shipment sides for the proposed shipping review. Both use the quota-validation component to compare declared caps, cached totals and reconstructed usage/reservations.

The application retrieves evidence, performs structured comparisons and quota calculations, and passes the supplied document and policy text to the LLM. The model returns a structured recommendation with cited facts and policy requirements. Responses receive source and output checks, with bounded correction attempts. The raw response and the resulting application recommendation remain distinguishable.

## Human decisions

An assessment does not issue a permit or authorize shipping. Authorized participants review findings and sign state-changing transactions separately. The model has no transaction-signing or database-update tools. Review records link decisions to their supporting assessment, while the smart contracts continue to enforce their encoded permissions and quantity conditions.

The ledger establishes a history of submitted actions. It does not independently establish document truth, physical shipment events or legal sufficiency. The prototype uses supplied synthetic policy text and structured records; operational adoption requires appropriate institutional authority, confidentiality and jurisdiction-specific requirements.
