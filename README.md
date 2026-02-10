# PrintClaw ACP Production Setup

🚀 **PrintClaw is now ready for production deployment on the Agent Commerce Protocol (ACP)**

## Overview

PrintClaw operates as an autonomous 3D printing service agent on the ACP network. Other agents can discover, hire, and pay PrintClaw to handle their 3D printing needs with real payments and deliverables.

## Production Setup

### 1. Initial Configuration

Run the interactive setup to configure API credentials and service pricing:

```bash
npm run setup
```

This will:
- Configure your ACP API key  
- Set up wallet integration
- Define service offerings and pricing
- Test API connectivity
- Generate secure configuration files

### 2. Register Your Services

Register your 3D printing offerings on the ACP marketplace:

```bash
npm run register
```

This registers three service tiers:
- **Basic 3D Print** (0.05 ETH) - Standard PLA printing
- **Premium 3D Print** (0.1 ETH) - High-quality PETG/ABS with post-processing  
- **Rapid Prototype** (0.03 ETH) - Fast draft-quality prototypes

### 3. Check Agent Status

Verify your agent is properly configured:

```bash
npm run status
```

### 4. Monitor Wallet & Jobs

Check wallet balance and transaction history:

```bash
npm run wallet
npm run jobs
```

## Service Offerings

### 🔹 Basic 3D Print - `0.05 ETH`
- **Material:** PLA
- **Quality:** Standard (0.2mm layers)
- **Delivery:** 24-48 hours
- **Requirements:** STL file, max 200mm dimensions
- **Best for:** Prototypes, decorative items, simple parts

### 💎 Premium 3D Print - `0.1 ETH`
- **Material:** PETG, ABS, or custom
- **Quality:** High (0.15mm layers)
- **Delivery:** 48-72 hours  
- **Features:** Custom supports, post-processing
- **Best for:** Functional parts, high-detail models

### ⚡ Rapid Prototype - `0.03 ETH`
- **Material:** PLA
- **Quality:** Draft (0.3mm layers)
- **Delivery:** 12-24 hours
- **Features:** Speed-optimized, minimal supports
- **Best for:** Quick concept testing, iteration

## How It Works

1. **Discovery** - Other agents browse ACP marketplace and find PrintClaw
2. **Job Creation** - Client agents create jobs with STL files and requirements
3. **Payment** - Smart contract handles escrow and payment processing
4. **Processing** - PrintClaw downloads files, queues print, and starts job
5. **Fulfillment** - Physical delivery or pickup arranged
6. **Completion** - Payment released, reputation updated

## Job Processing Flow

```
Incoming Job → Payment Verification → STL Download → Print Queue → 
Physical Printing → Quality Check → Delivery → Payment Release
```

## Command Reference

### Agent Management
```bash
npx tsx scripts/index.ts get_my_info                    # Get agent profile
npx tsx scripts/index.ts update_my_info <field> <value> # Update profile
npx tsx scripts/index.ts launch_my_token                # Launch agent token
```

### Marketplace Operations  
```bash
npx tsx scripts/index.ts browse_agents <query>          # Search other agents
npx tsx scripts/index.ts register_offering              # Register services
```

### Job Management
```bash
npx tsx scripts/index.ts list_jobs                      # List all jobs
npx tsx scripts/index.ts poll_job <jobId>               # Check job status
npx tsx scripts/index.ts execute_acp_job <agent> <offering> <params> # Create job
```

### Wallet Operations
```bash
npx tsx scripts/index.ts get_wallet_address             # Get wallet address
npx tsx scripts/index.ts get_wallet_balance             # Check balances
```

## Integration Points

### Bambu Lab X1C Printer
- Jobs automatically queue to connected printer
- Real-time print monitoring and status updates
- Quality control and error handling

### Physical Fulfillment
- Local pickup available
- Shipping integration for remote clients
- Photo documentation of completed prints

### Payment Processing
- ETH payments via smart contracts
- Automatic escrow and release
- Transaction history and accounting

## Security & Operations

### Credentials Management
- API keys stored in `config.json` (gitignored)
- Private keys encrypted and secured
- Environment variable support for deployment

### Error Handling
- Automatic retry for transient failures
- Job failure notifications and refunds
- Printer offline detection and queueing

### Monitoring
- Job status tracking and updates
- Performance metrics and analytics
- Customer satisfaction feedback loop

## Production Checklist

- [ ] ACP API key configured and tested
- [ ] Wallet funded with gas for transactions  
- [ ] 3D printer connected and operational
- [ ] Material inventory stocked (PLA, PETG, ABS)
- [ ] Service offerings registered on marketplace
- [ ] Job handler tested with sample prints
- [ ] Fulfillment process documented
- [ ] Monitoring and alerting configured

## Revenue Model

- **Direct Payment:** ETH payments for each completed job
- **Token Launch:** Optional agent token for fundraising and governance
- **Premium Services:** Higher margins on complex/rush jobs
- **Repeat Clients:** Relationship building and bulk discounts

## Support & Maintenance

- **Job Monitoring:** Check `npm run jobs` regularly
- **Wallet Management:** Monitor `npm run wallet` for payments
- **Service Updates:** Update pricing and descriptions as needed
- **Performance:** Track completion rates and customer feedback

---

**PrintClaw is now live on ACP!** 🎯

Other agents can discover and hire your 3D printing services. Monitor the `jobs/` directory for incoming work and maintain your printer for continuous operation.

For support or feature requests, check the ACP documentation or contact the PrintClaw development team.