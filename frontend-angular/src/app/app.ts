import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from './api.service';
import { MultiSelectModule } from 'primeng/multiselect';
import { FileUploadModule } from 'primeng/fileupload';
import { ButtonModule } from 'primeng/button';
import { TableModule } from 'primeng/table';
import { ToastModule } from 'primeng/toast';
import { TagModule } from 'primeng/tag';
import { ProgressBarModule } from 'primeng/progressbar';
import { DialogModule } from 'primeng/dialog';
import { MessageService } from 'primeng/api';
import { Subscription, interval, switchMap } from 'rxjs';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    CommonModule, 
    FormsModule, 
    MultiSelectModule, 
    FileUploadModule, 
    ButtonModule, 
    TableModule,
    ToastModule,
    TagModule,
    ProgressBarModule,
    DialogModule
  ],
  providers: [MessageService],
  templateUrl: './app.html',
  styleUrl: './app.scss'
})
export class App implements OnInit {
  private api = inject(ApiService);
  private messageService = inject(MessageService);

  actTypes = signal<any[]>([]);
  selectedActs = signal<any[]>([]);
  files = signal<File[]>([]);
  extractions = signal<any[]>([]);
  activeTasks = signal<any[]>([]);
  submitting = signal(false);
  
  view = signal<'dashboard' | 'history'>('dashboard');
  
  // Detalle de resultados
  displayResult = signal(false);
  selectedExtraction = signal<any>(null);

  private pollingSub?: Subscription;

  ngOnInit() {
    this.loadActs();
    this.loadHistory();
    this.startPolling();
  }

  loadActs() {
    this.api.getActs().subscribe({
      next: (data) => {
          // Mapear para visualización
          const mapped = data.acts.map((a: any) => ({
              ...a,
              display_label: `${a.form_code} - ${a.dsactocorta}`
          }));
          this.actTypes.set(mapped);
      },
      error: () => this.messageService.add({ severity: 'error', summary: 'Error', detail: 'No se pudo conectar con el catálogo.' })
    });
  }

  loadHistory() {
    this.api.getExtractions().subscribe(data => this.extractions.set(data.extractions));
  }

  onSelectFiles(event: any) {
    this.files.set(Array.from(event.currentFiles));
  }

  startExtraction() {
    if (this.selectedActs().length === 0 || this.files().length === 0) return;

    this.submitting.set(true);
    const fd = new FormData();
    
    // Unir actos por coma para el backend multi-acto
    const acts = this.selectedActs().map(a => a.dsacto).join(',');
    const codes = this.selectedActs().map(a => a.form_code).join(',');
    
    fd.append('act_type', acts);
    fd.append('form_code', codes);
    this.files().forEach(f => fd.append('document', f));

    this.api.processDocument(fd).subscribe({
      next: (res: any) => {
        this.messageService.add({ severity: 'success', summary: 'Proceso Iniciado', detail: res.message });
        res.task_ids.forEach((id: string) => {
          this.activeTasks.set([...this.activeTasks(), { taskId: id, status: 'PENDING_CELERY', progress: 0 }]);
        });
        this.files.set([]);
        this.selectedActs.set([]);
        this.submitting.set(false);
      },
      error: () => {
        this.submitting.set(false);
        this.messageService.add({ severity: 'error', summary: 'Error', detail: 'Fallo al iniciar extracción.' });
      }
    });
  }

  startPolling() {
      this.pollingSub = interval(4000).pipe(
          switchMap(() => {
              const tasks = this.activeTasks().filter(t => t.status !== 'COMPLETED' && t.status !== 'ERROR');
              if (tasks.length === 0) return Promise.resolve([]);
              return Promise.all(tasks.map(t => this.api.getProgress(t.taskId).toPromise()));
          })
      ).subscribe((results: any) => {
          if (!results || results.length === 0) return;

          const updated = this.activeTasks().map(t => {
              const res = results.find((r: any) => r?.task_id === t.taskId);
              if (res) {
                  return { 
                      ...t, 
                      status: res.status, 
                      progress: res.progress_pct, 
                      stage: res.stage_label,
                      fileName: res.file_name
                  };
              }
              return t;
          });
          this.activeTasks.set(updated);
          
          if (results.some((r: any) => r?.finished)) {
              this.loadHistory();
          }
      });
  }

  viewResult(ex: any) {
      this.selectedExtraction.set(ex);
      this.displayResult.set(true);
  }

  downloadJSON(ex: any) {
      const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(ex.extracted_data || ex.simplified_json, null, 2));
      const downloadAnchorNode = document.createElement('a');
      downloadAnchorNode.setAttribute("href", dataStr);
      downloadAnchorNode.setAttribute("download", `idp_${ex.expediente_id}_${ex.form_code}.json`);
      document.body.appendChild(downloadAnchorNode);
      downloadAnchorNode.click();
      downloadAnchorNode.remove();
  }

  deleteExtraction(ex: any) {
      if (confirm('¿Seguro que deseas eliminar este registro?')) {
          this.api.deleteExtraction(ex.task_id).subscribe(() => {
              this.loadHistory();
              this.messageService.add({ severity: 'info', summary: 'Eliminado', detail: 'Registro borrado.' });
          });
      }
  }

  getStageSeverity(status: string) {
      if (status === 'COMPLETED' || status === 'COMPLETADO') return 'success';
      if (status.includes('ERROR') || status === 'FAILED') return 'danger';
      return 'info';
  }
}
