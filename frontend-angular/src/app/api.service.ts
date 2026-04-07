import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

@Injectable({
  providedIn: 'root'
})
export class ApiService {
  private http = inject(HttpClient);
  private apiBase = `http://${window.location.hostname}:8000/api/v1`;

  getActs(): Observable<any> {
    return this.http.get(`${this.apiBase}/forms`);
  }

  processDocument(formData: FormData): Observable<any> {
    return this.http.post(`${this.apiBase}/process`, formData);
  }

  getExtractions(): Observable<any> {
    return this.http.get(`${this.apiBase}/extractions`);
  }

  getProgress(taskId: string): Observable<any> {
    return this.http.get(`${this.apiBase}/progress/${taskId}`);
  }

  deleteExtraction(taskId: string): Observable<any> {
    return this.http.delete(`${this.apiBase}/extractions/${taskId}`);
  }
}
