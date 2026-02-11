#!/usr/bin/env node

import { writeFileSync, mkdirSync, existsSync } from 'fs';
import { join } from 'path';
import axios from 'axios';

interface PrintJob {
  jobId: string;
  agentId: string;
  offeringId: string;
  parameters: {
    stlUrl?: string;
    stlFile?: string;
    specifications?: string;
    material?: string;
    quantity?: number;
    priority?: string;
    notes?: string;
  };
  payment: {
    amount: string;
    currency: string;
    confirmed: boolean;
  };
}

class PrintJobHandler {
  private outputDir: string;

  constructor() {
    this.outputDir = join(process.cwd(), 'jobs');
    if (!existsSync(this.outputDir)) {
      mkdirSync(this.outputDir, { recursive: true });
    }
  }

  async handleBasicPrint(job: PrintJob) {
    console.log(`🔄 Processing basic print job: ${job.jobId}`);
    
    try {
      // Download STL file
      const stlPath = await this.downloadSTL(job);
      
      // Prepare print parameters
      const printParams = {
        material: job.parameters.material || 'PLA',
        quality: 'standard',
        infill: 15,
        supports: 'auto',
        layer_height: 0.2,
        print_speed: 'normal'
      };
      
      // Start print via PrintClaw's printer integration
      await this.startPrint(stlPath, printParams, job);
      
      return {
        status: 'processing',
        message: 'Print job started successfully',
        estimatedCompletion: this.calculateETA(24, 48), // 24-48 hours
        tracking: {
          jobId: job.jobId,
          stage: 'printing',
          progress: 0
        }
      };
    } catch (error: any) {
      throw new Error(`Basic print failed: ${error.message}`);
    }
  }

  async handlePremiumPrint(job: PrintJob) {
    console.log(`🔄 Processing premium print job: ${job.jobId}`);
    
    try {
      const stlPath = await this.downloadSTL(job);
      
      // Premium print parameters
      const printParams = {
        material: job.parameters.material || 'PETG',
        quality: 'high',
        infill: 25,
        supports: 'custom',
        layer_height: 0.15,
        print_speed: 'slow',
        postProcessing: true
      };
      
      await this.startPrint(stlPath, printParams, job);
      
      return {
        status: 'processing',
        message: 'Premium print job started with custom settings',
        estimatedCompletion: this.calculateETA(48, 72), // 48-72 hours
        tracking: {
          jobId: job.jobId,
          stage: 'printing',
          progress: 0,
          features: ['high_quality', 'post_processing', 'custom_supports']
        }
      };
    } catch (error: any) {
      throw new Error(`Premium print failed: ${error.message}`);
    }
  }

  async handleRapidPrototype(job: PrintJob) {
    console.log(`🔄 Processing rapid prototype job: ${job.jobId}`);
    
    try {
      const stlPath = await this.downloadSTL(job);
      
      // Rapid prototype parameters - optimized for speed
      const printParams = {
        material: 'PLA',
        quality: 'draft',
        infill: 10,
        supports: 'minimal',
        layer_height: 0.3,
        print_speed: 'fast'
      };
      
      await this.startPrint(stlPath, printParams, job);
      
      return {
        status: 'processing',
        message: 'Rapid prototype started - optimized for speed',
        estimatedCompletion: this.calculateETA(12, 24), // 12-24 hours
        tracking: {
          jobId: job.jobId,
          stage: 'printing',
          progress: 0,
          features: ['fast_printing', 'draft_quality']
        }
      };
    } catch (error: any) {
      throw new Error(`Rapid prototype failed: ${error.message}`);
    }
  }

  private async downloadSTL(job: PrintJob): Promise<string> {
    const stlUrl = job.parameters.stlUrl;
    if (!stlUrl) {
      throw new Error('No STL file URL provided');
    }

    console.log(`📥 Downloading STL from: ${stlUrl}`);
    
    const response = await axios.get(stlUrl, {
      responseType: 'arraybuffer',
      timeout: 30000
    });

    const fileName = `${job.jobId}.stl`;
    const filePath = join(this.outputDir, fileName);
    
    writeFileSync(filePath, response.data);
    console.log(`✅ STL downloaded: ${filePath}`);
    
    return filePath;
  }

  private async startPrint(stlPath: string, params: any, job: PrintJob) {
    console.log(`🖨️ Starting print with parameters:`, params);
    
    // This would integrate with your actual printer API
    // For now, we'll simulate the integration
    const printCommand = {
      action: 'print_model',
      model_url: `file://${stlPath}`,
      filename: `${job.jobId}_print.3mf`,
      settings: params
    };

    // Save job status
    const jobStatus = {
      jobId: job.jobId,
      status: 'printing',
      startTime: new Date().toISOString(),
      parameters: params,
      filePath: stlPath,
      estimatedDuration: this.estimatePrintTime(params),
      printCommand
    };

    const statusPath = join(this.outputDir, `${job.jobId}_status.json`);
    writeFileSync(statusPath, JSON.stringify(jobStatus, null, 2));
    
    console.log(`✅ Print job queued: ${job.jobId}`);
    console.log(`📁 Status saved: ${statusPath}`);
    
    // Here you would actually call your printer API
    // await printerAPI.startPrint(printCommand);
  }

  private calculateETA(minHours: number, maxHours: number): string {
    const avgHours = (minHours + maxHours) / 2;
    const eta = new Date(Date.now() + avgHours * 60 * 60 * 1000);
    return eta.toISOString();
  }

  private estimatePrintTime(params: any): number {
    // Simple estimation based on quality and speed settings
    let baseTime = 8; // base 8 hours
    
    if (params.quality === 'high') baseTime *= 1.5;
    if (params.quality === 'draft') baseTime *= 0.6;
    if (params.print_speed === 'slow') baseTime *= 1.3;
    if (params.print_speed === 'fast') baseTime *= 0.7;
    
    return Math.round(baseTime);
  }

  async processJob(job: PrintJob) {
    console.log(`🎯 Processing job ${job.jobId} for offering ${job.offeringId}`);
    
    // Verify payment
    if (!job.payment.confirmed) {
      throw new Error('Payment not confirmed');
    }

    // Route to appropriate handler
    switch (job.offeringId) {
      case 'basic-3d-print':
        return await this.handleBasicPrint(job);
      
      case 'premium-3d-print':
        return await this.handlePremiumPrint(job);
      
      case 'rapid-prototype':
        return await this.handleRapidPrototype(job);
      
      default:
        throw new Error(`Unknown offering: ${job.offeringId}`);
    }
  }
}

// CLI usage
async function main() {
  const args = process.argv.slice(2);
  
  if (args.length < 1) {
    console.log(JSON.stringify({ error: 'Usage: job-handler.ts <jobData>' }));
    process.exit(1);
  }

  try {
    const jobData = JSON.parse(args[0]);
    const handler = new PrintJobHandler();
    const result = await handler.processJob(jobData);
    
    console.log(JSON.stringify({
      success: true,
      jobId: jobData.jobId,
      result
    }));
  } catch (error: any) {
    console.log(JSON.stringify({
      success: false,
      error: error.message
    }));
    process.exit(1);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}

export { PrintJobHandler };