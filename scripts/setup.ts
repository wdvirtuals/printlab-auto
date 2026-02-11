#!/usr/bin/env node

import { writeFileSync, readFileSync, existsSync } from 'fs';
import { join } from 'path';
import { createInterface } from 'readline';
import axios from 'axios';
import { randomBytes } from 'crypto';

const rl = createInterface({
  input: process.stdin,
  output: process.stdout,
});

function question(prompt: string): Promise<string> {
  return new Promise((resolve) => {
    rl.question(prompt, resolve);
  });
}

async function main() {
  console.log('\n🔧 PrintClaw ACP Production Setup');
  console.log('==================================');
  
  console.log('\nThis will configure PrintClaw to operate on the live ACP network.');
  console.log('You\'ll be able to receive real jobs and payments from other agents.\n');

  // Check if already configured
  const configPath = join(process.cwd(), 'config.json');
  if (existsSync(configPath)) {
    const overwrite = await question('Config already exists. Overwrite? (y/N): ');
    if (overwrite.toLowerCase() !== 'y') {
      console.log('Setup cancelled.');
      rl.close();
      return;
    }
  }

  // Get agent details
  console.log('\n📝 Agent Configuration:');
  const agentName = await question('Agent name [PrintClaw 3D Printing Service]: ') || 'PrintClaw 3D Printing Service';
  
  const agentDescription = await question('Agent description [Professional 3D printing with Bambu Lab X1C]: ') || 
    'Professional 3D printing services with Bambu Lab X1C printer. High-quality PLA, PETG, ABS printing. Fast turnaround, competitive pricing.';

  // Pricing configuration
  console.log('\n💰 Service Pricing (in ETH):');
  const basicPrice = await question('Basic 3D Print price [0.05]: ') || '0.05';
  const premiumPrice = await question('Premium 3D Print price [0.1]: ') || '0.1';
  const prototypePrice = await question('Rapid Prototype price [0.03]: ') || '0.03';

  // API Configuration
  console.log('\n🔑 API Configuration:');
  console.log('Visit https://claw-api.virtuals.io to get your API credentials');
  
  const apiKey = await question('ACP API Key (required): ');
  if (!apiKey) {
    console.log('❌ API key is required. Setup cancelled.');
    rl.close();
    return;
  }

  const walletPrivateKey = await question('Wallet private key (optional, will generate if empty): ');
  const finalWalletKey = walletPrivateKey || randomBytes(32).toString('hex');

  // Test API connection
  console.log('\n🧪 Testing API connection...');
  try {
    const response = await axios.get('https://claw-api.virtuals.io/health', {
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json'
      }
    });
    console.log('✅ API connection successful');
  } catch (error: any) {
    console.log('❌ API connection failed:', error.message);
    const proceed = await question('Continue anyway? (y/N): ');
    if (proceed.toLowerCase() !== 'y') {
      console.log('Setup cancelled.');
      rl.close();
      return;
    }
  }

  // Generate config
  const config = {
    apiKey,
    endpoint: 'https://claw-api.virtuals.io',
    agentName,
    agentDescription,
    walletPrivateKey: finalWalletKey,
    services: {
      basicPrint: {
        name: 'Basic 3D Print',
        price: basicPrice,
        description: 'Standard PLA printing for small to medium objects. Fast turnaround, good quality.',
        requirements: 'STL file, dimensions under 200mm in any axis'
      },
      premiumPrint: {
        name: 'Premium 3D Print',
        price: premiumPrice,
        description: 'High-quality PETG/ABS printing with custom settings and post-processing.',
        requirements: 'STL file, detailed specifications'
      },
      rapidPrototype: {
        name: 'Rapid Prototype',
        price: prototypePrice,
        description: 'Quick prototype printing for testing and iteration. Basic quality, very fast.',
        requirements: 'STL file, simple geometry preferred'
      }
    },
    production: true,
    setupDate: new Date().toISOString()
  };

  // Save config
  writeFileSync(configPath, JSON.stringify(config, null, 2));
  console.log(`\n✅ Configuration saved to ${configPath}`);

  // Create .env backup
  const envContent = `ACP_API_KEY=${apiKey}
ACP_ENDPOINT=https://claw-api.virtuals.io
WALLET_PRIVATE_KEY=${finalWalletKey}
AGENT_NAME=${agentName}
PRODUCTION=true
`;
  writeFileSync(join(process.cwd(), '.env'), envContent);
  console.log('✅ Environment variables saved to .env');

  console.log('\n🚀 Production Setup Complete!');
  console.log('\nNext steps:');
  console.log('1. Run: npm run register - Register your services on ACP');
  console.log('2. Run: npm run status - Check your agent status');
  console.log('3. Monitor for incoming jobs from other agents');
  
  console.log('\n⚠️  Security Notes:');
  console.log('- Keep your API key and private key secure');
  console.log('- Add .env to .gitignore if not already done');
  console.log('- Consider using environment variables for production deployment');

  rl.close();
}

main().catch(error => {
  console.error('Setup failed:', error.message);
  rl.close();
  process.exit(1);
});