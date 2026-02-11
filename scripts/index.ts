#!/usr/bin/env node

import { readFileSync, existsSync, writeFileSync } from 'fs';
import { join } from 'path';
import axios from 'axios';
import { createHash } from 'crypto';

// Load config
function loadConfig() {
  const configPath = join(process.cwd(), 'config.json');
  const envPath = join(process.cwd(), '.env');
  
  if (existsSync(configPath)) {
    const config = JSON.parse(readFileSync(configPath, 'utf8'));
    return config;
  }
  
  if (existsSync(envPath)) {
    const envContent = readFileSync(envPath, 'utf8');
    const config: any = {};
    envContent.split('\n').forEach(line => {
      if (line.includes('=')) {
        const [key, value] = line.split('=', 2);
        config[key.trim()] = value.trim().replace(/['"]/g, '');
      }
    });
    return {
      apiKey: config.ACP_API_KEY,
      endpoint: config.ACP_ENDPOINT || 'https://claw-api.virtuals.io',
      agentName: config.AGENT_NAME || 'PrintClaw 3D Printing Service',
      walletPrivateKey: config.WALLET_PRIVATE_KEY,
      production: config.PRODUCTION === 'true'
    };
  }
  
  throw new Error('No config found. Run npm run setup first.');
}

// API client
class ACPClient {
  private apiKey: string;
  private endpoint: string;
  
  constructor(apiKey: string, endpoint: string) {
    this.apiKey = apiKey;
    this.endpoint = endpoint;
  }

  async request(method: string, path: string, data?: any) {
    try {
      const response = await axios({
        method,
        url: `${this.endpoint}${path}`,
        headers: {
          'Authorization': `Bearer ${this.apiKey}`,
          'Content-Type': 'application/json',
        },
        data
      });
      return response.data;
    } catch (error: any) {
      if (error.response) {
        throw new Error(`API Error: ${error.response.status} - ${error.response.data?.message || error.response.statusText}`);
      } else if (error.request) {
        throw new Error('Network error: Unable to reach ACP API');
      } else {
        throw new Error(`Request error: ${error.message}`);
      }
    }
  }

  async browseAgents(query: string) {
    return this.request('GET', `/agents/search?q=${encodeURIComponent(query)}`);
  }

  async getMyInfo() {
    return this.request('GET', '/agent/me');
  }

  async updateMyInfo(field: string, value: string) {
    return this.request('PATCH', '/agent/me', { [field]: value });
  }

  async registerOffering(offering: any) {
    return this.request('POST', '/agent/offerings', offering);
  }

  async getWallet() {
    return this.request('GET', '/agent/wallet');
  }

  async executeJob(jobId: string, agentId: string, offeringId: string, parameters: any) {
    return this.request('POST', '/jobs', {
      jobId,
      targetAgent: agentId,
      offering: offeringId,
      parameters
    });
  }

  async pollJob(jobId: string) {
    return this.request('GET', `/jobs/${jobId}`);
  }

  async listJobs() {
    return this.request('GET', '/agent/jobs');
  }
}

// Command implementations
async function browseAgents(client: ACPClient, query: string) {
  const result = await client.browseAgents(query);
  console.log(JSON.stringify(result));
}

async function getMyInfo(client: ACPClient) {
  const result = await client.getMyInfo();
  console.log(JSON.stringify(result));
}

async function updateMyInfo(client: ACPClient, field: string, value: string) {
  const result = await client.updateMyInfo(field, value);
  console.log(JSON.stringify(result));
}

async function getWalletAddress(client: ACPClient) {
  const result = await client.getWallet();
  console.log(JSON.stringify({ address: result.address }));
}

async function getWalletBalance(client: ACPClient) {
  const result = await client.getWallet();
  console.log(JSON.stringify({ balances: result.balances || [] }));
}

async function executeACPJob(client: ACPClient, agentId: string, offeringId: string, parameters: string) {
  const jobId = `job_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  let parsedParams = {};
  
  try {
    parsedParams = JSON.parse(parameters);
  } catch {
    parsedParams = { request: parameters };
  }

  const result = await client.executeJob(jobId, agentId, offeringId, parsedParams);
  console.log(JSON.stringify(result));
  
  // Auto-poll for completion
  if (result.jobId) {
    await pollJob(client, result.jobId);
  }
}

async function pollJob(client: ACPClient, jobId: string) {
  let attempts = 0;
  const maxAttempts = 60; // 5 minutes max
  
  while (attempts < maxAttempts) {
    const result = await client.pollJob(jobId);
    
    if (result.status === 'completed' || result.status === 'failed' || result.status === 'rejected') {
      console.log(JSON.stringify(result));
      return;
    }
    
    attempts++;
    await new Promise(resolve => setTimeout(resolve, 5000)); // Wait 5 seconds
  }
  
  console.log(JSON.stringify({ error: 'Job polling timeout', jobId }));
}

async function listJobs(client: ACPClient) {
  const result = await client.listJobs();
  console.log(JSON.stringify(result));
}

async function registerOffering(client: ACPClient, config: any) {
  const offerings = [
    {
      id: 'basic-3d-print',
      name: config.services?.basicPrint?.name || 'Basic 3D Print',
      description: config.services?.basicPrint?.description || 'Standard PLA printing for small to medium objects. Fast turnaround, good quality.',
      price: config.services?.basicPrint?.price || '0.05',
      currency: 'ETH',
      category: '3d-printing',
      requirements: config.services?.basicPrint?.requirements || 'STL file, dimensions under 200mm in any axis',
      deliveryTime: '24-48 hours',
      handler: 'handle_basic_print'
    },
    {
      id: 'premium-3d-print',
      name: config.services?.premiumPrint?.name || 'Premium 3D Print',
      description: config.services?.premiumPrint?.description || 'High-quality PETG/ABS printing with custom settings and post-processing.',
      price: config.services?.premiumPrint?.price || '0.1',
      currency: 'ETH',
      category: '3d-printing',
      requirements: config.services?.premiumPrint?.requirements || 'STL file, detailed specifications',
      deliveryTime: '48-72 hours',
      handler: 'handle_premium_print'
    },
    {
      id: 'rapid-prototype',
      name: config.services?.rapidPrototype?.name || 'Rapid Prototype',
      description: config.services?.rapidPrototype?.description || 'Quick prototype printing for testing and iteration. Basic quality, very fast.',
      price: config.services?.rapidPrototype?.price || '0.03',
      currency: 'ETH',
      category: '3d-printing',
      requirements: config.services?.rapidPrototype?.requirements || 'STL file, simple geometry preferred',
      deliveryTime: '12-24 hours',
      handler: 'handle_rapid_prototype'
    }
  ];

  const results = [];
  for (const offering of offerings) {
    try {
      const result = await client.registerOffering(offering);
      results.push({ ...offering, status: 'registered', result });
    } catch (error: any) {
      results.push({ ...offering, status: 'failed', error: error.message });
    }
  }

  console.log(JSON.stringify({
    success: true,
    registered: results.filter(r => r.status === 'registered').length,
    failed: results.filter(r => r.status === 'failed').length,
    offerings: results
  }));
}

async function launchMyToken(client: ACPClient, config: any) {
  try {
    const tokenData = {
      name: `${config.agentName} Token`,
      symbol: 'PCLAW',
      description: `Token for ${config.agentName} - enabling governance and revenue sharing from 3D printing services`,
      initialSupply: '1000000',
      mintable: false
    };
    
    const result = await client.request('POST', '/agent/token', tokenData);
    console.log(JSON.stringify(result));
  } catch (error: any) {
    console.log(JSON.stringify({ error: error.message }));
  }
}

// Main CLI handler
async function main() {
  const args = process.argv.slice(2);
  const command = args[0];

  if (!command) {
    console.log(JSON.stringify({ error: "No command specified" }));
    process.exit(1);
  }

  try {
    const config = loadConfig();
    const client = new ACPClient(config.apiKey, config.endpoint);
    
    switch (command) {
      case 'browse_agents':
        await browseAgents(client, args[1] || '3d printing');
        break;
      
      case 'execute_acp_job':
        if (args.length < 4) {
          console.log(JSON.stringify({ error: "Usage: execute_acp_job <agentId> <offeringId> <parameters>" }));
          process.exit(1);
        }
        await executeACPJob(client, args[1], args[2], args[3]);
        break;
      
      case 'poll_job':
        if (args.length < 2) {
          console.log(JSON.stringify({ error: "Usage: poll_job <jobId>" }));
          process.exit(1);
        }
        await pollJob(client, args[1]);
        break;
      
      case 'get_my_info':
        await getMyInfo(client);
        break;
        
      case 'update_my_info':
        if (args.length < 3) {
          console.log(JSON.stringify({ error: "Usage: update_my_info <field> <value>" }));
          process.exit(1);
        }
        await updateMyInfo(client, args[1], args[2]);
        break;
      
      case 'get_wallet_address':
        await getWalletAddress(client);
        break;
        
      case 'get_wallet_balance':
        await getWalletBalance(client);
        break;

      case 'register_offering':
        await registerOffering(client, config);
        break;
        
      case 'launch_my_token':
        await launchMyToken(client, config);
        break;
      
      case 'list_jobs':
        await listJobs(client);
        break;
      
      default:
        console.log(JSON.stringify({ error: `Unknown command: ${command}` }));
        process.exit(1);
    }
  } catch (error: any) {
    console.log(JSON.stringify({ error: error.message }));
    process.exit(1);
  }
}

main().catch(error => {
  console.log(JSON.stringify({ error: error.message }));
  process.exit(1);
});