$(document).ready(function() {
  const API_BASE = '/api';

  // Set up Authorization header untuk seluruh request AJAX
  $.ajaxSetup({
    beforeSend: function(xhr) {
      const token = localStorage.getItem("jwt_token");
      if (token) {
        xhr.setRequestHeader('Authorization', 'Bearer ' + token);
      }
    }
  });

  // Tangani kegagalan autentikasi (401) secara global
  $(document).ajaxError(function(event, jqXHR, ajaxSettings, thrownError) {
    if (jqXHR.status === 401) {
      localStorage.removeItem("jwt_token");
      window.location.href = "login.html";
    }
  });

  // Handler Tombol Logout
  $("#btn-logout").click(function(e) {
    e.preventDefault();
    if (confirm("Apakah Anda yakin ingin keluar dari dashboard?")) {
      localStorage.removeItem("jwt_token");
      window.location.href = "login.html";
    }
  });

  // 1. View Router
  function showView(viewId, title) {
    $("#page-title").text(title);
    $(".nav-sidebar .nav-link").removeClass("active");
    
    // Hide all views
    $("#view-dashboard").hide();
    $("#view-orders").hide();
    $("#view-dicom").hide();
    $("#view-logs").hide();
    $("#view-settings").hide();
    
    // Show selected view
    $("#" + viewId).show();
  }

  $("#nav-dashboard").click(function(e) {
    e.preventDefault();
    showView("view-dashboard", "Dashboard Overview");
    $(this).addClass("active");
    loadStats();
  });

  $("#nav-orders").click(function(e) {
    e.preventDefault();
    showView("view-orders", "Order Monitoring");
    $(this).addClass("active");
    loadOrders();
  });

  $("#nav-dicom").click(function(e) {
    e.preventDefault();
    showView("view-dicom", "DICOM Studies");
    $(this).addClass("active");
    loadDicom();
  });

  $("#nav-logs").click(function(e) {
    e.preventDefault();
    showView("view-logs", "SATUSEHAT Logs");
    $(this).addClass("active");
    loadLogs();
  });

  $("#nav-settings").click(function(e) {
    e.preventDefault();
    showView("view-settings", "System Settings");
    $(this).addClass("active");
    loadSettings();
  });

  // 2. Fetch Stats
  function loadStats() {
    $.getJSON(`${API_BASE}/stats`, function(res) {
      if (res.success) {
        $("#stat-total-orders").text(res.data.total_orders);
        $("#stat-success-uploads").text(res.data.success_uploads);
        $("#stat-failed-uploads").text(res.data.failed_uploads);
        $("#stat-mwl-queries").text(res.data.mwl_queries);
      }
    }).fail(function(err) {
      console.error("Gagal memuat statistik:", err);
    });
  }

  // 3. Fetch Orders list
  function loadOrders() {
    $("#orders-table-body").html('<tr><td colspan="8" class="text-center text-muted py-4"><i class="fas fa-spinner fa-spin mr-2"></i>Memuat data...</td></tr>');
    $.getJSON(`${API_BASE}/orders`, function(res) {
      if (res.success && res.data.length > 0) {
        let html = '';
        res.data.forEach(order => {
          let localBadge = '';
          if (order.status === 'COMPLETED') {
            localBadge = '<span class="badge badge-success">COMPLETED</span>';
          } else if (order.status === 'SCHEDULED') {
            localBadge = '<span class="badge badge-info">SCHEDULED</span>';
          } else {
            localBadge = '<span class="badge badge-warning">PENDING</span>';
          }

          let satsetBadge = '';
          if (order.satusehat_report_status === 'SENT') {
            satsetBadge = '<span class="badge badge-success-glow">SENT (Report)</span>';
          } else if (order.satusehat_servicerequest_status === 'SENT') {
            satsetBadge = '<span class="badge badge-info-glow">SENT (ServiceRequest)</span>';
          } else if (order.satusehat_servicerequest_status === 'FAILED' || order.satusehat_report_status === 'FAILED') {
            satsetBadge = '<span class="badge badge-danger-glow">FAILED (Needs Retry)</span>';
          } else {
            satsetBadge = '<span class="badge badge-secondary">UNSENT</span>';
          }

          let formattedDate = order.order_datetime ? new Date(order.order_datetime).toLocaleString('id-ID') : '-';

          html += `
            <tr>
              <td><strong>${order.noreg || '-'}</strong></td>
              <td><strong>${order.accession_number}</strong></td>
              <td>${order.patient_id}</td>
              <td>${order.patient_name}</td>
              <td><span class="badge badge-primary">${order.modality}</span></td>
              <td>${order.doctor_name}</td>
              <td>${formattedDate}</td>
              <td>${localBadge}</td>
              <td>${satsetBadge}</td>
            </tr>
          `;
        });
        $("#orders-table-body").html(html);
      } else {
        $("#orders-table-body").html('<tr><td colspan="9" class="text-center text-muted py-4">Belum ada data order radiologi.</td></tr>');
      }
    }).fail(function() {
      $("#orders-table-body").html('<tr><td colspan="9" class="text-center text-danger py-4"><i class="fas fa-exclamation-circle mr-2"></i>Gagal memuat data dari server.</td></tr>');
    });
  }

  // 4. Fetch DICOM Studies list
  function loadDicom() {
    $("#dicom-table-body").html('<tr><td colspan="8" class="text-center text-muted py-4"><i class="fas fa-spinner fa-spin mr-2"></i>Memuat data...</td></tr>');
    $.getJSON(`${API_BASE}/dicom`, function(res) {
      if (res.success && res.data.length > 0) {
        let html = '';
        res.data.forEach(study => {
          let dateStr = study.created_at ? new Date(study.created_at).toLocaleString('id-ID') : '-';
          let wadoLink = '-';
          if (study.satusehat_status === 'UPLOADED' || study.satusehat_status === 'UPLOAD_PARTIAL') {
            // WADO-URI ke SATUSEHAT Production (hanya tampil jika sudah berhasil diupload)
            let wadoUrl = `https://api.kemkes.go.id/dicom-web/wado?requestType=WADO&studyUID=${study.study_instance_uid}`;
            wadoLink = `<a href="${wadoUrl}" target="_blank" class="btn btn-xs btn-primary"><i class="fas fa-external-link-alt"></i> SATUSEHAT WADO</a>`;
          }

          let actionBtn = `
            <button class="btn btn-xs btn-success btn-view-study" data-uid="${study.study_instance_uid}" data-acc="${study.accession_number}" title="Buka viewer lokal (Cornerstone)">
              <i class="fas fa-eye mr-1"></i> Local View
            </button>
          `;

          html += `
            <tr>
               <td><strong>${study.accession_number}</strong></td>
               <td><code style="font-size:10px;">${study.study_instance_uid}</code></td>
               <td>${study.series_count}</td>
               <td>${study.sop_count}</td>
               <td><code style="font-size:10px;">${study.storage_path || '-'}</code></td>
               <td>${wadoLink}</td>
               <td>${dateStr}</td>
               <td>${actionBtn}</td>
            </tr>
          `;
        });
        $("#dicom-table-body").html(html);

        // Bind click event untuk local cornerstone viewer
        $(".btn-view-study").click(function() {
          const studyUid = $(this).data("uid");
          const acc = $(this).data("acc");
          openDicomViewer(studyUid, acc);
        });
      } else {
        $("#dicom-table-body").html('<tr><td colspan="8" class="text-center text-muted py-4">Belum ada citra DICOM yang diarsipkan.</td></tr>');
      }
    }).fail(function() {
      $("#dicom-table-body").html('<tr><td colspan="8" class="text-center text-danger py-4"><i class="fas fa-exclamation-circle mr-2"></i>Gagal memuat data DICOM.</td></tr>');
    });
  }

  // 5. Fetch Integration Logs & Handlers
  function loadLogs() {
    $("#logs-table-body").html('<tr><td colspan="6" class="text-center text-muted py-4"><i class="fas fa-spinner fa-spin mr-2"></i>Memuat data...</td></tr>');
    $.getJSON(`${API_BASE}/logs`, function(res) {
      if (res.success && res.data.length > 0) {
        let html = '';
        res.data.forEach(log => {
          let dateStr = log.created_at ? new Date(log.created_at).toLocaleString('id-ID') : '-';
          let statusBadge = log.status === 'SUCCESS' ? '<span class="badge badge-success">SUCCESS</span>' : '<span class="badge badge-danger">FAILED</span>';
          let actionBtn = '-';
          
          if (log.status === 'FAILED') {
            actionBtn = `<button class="btn btn-xs btn-warning btn-retry" data-acc="${log.accession_number}"><i class="fas fa-sync mr-1"></i>Retry</button>`;
          }

          let tooltipError = log.error_message ? `title="${log.error_message.replace(/"/g, '&quot;')}" style="cursor:help; border-bottom: 1px dashed red;"` : '';

          html += `
            <tr>
              <td>${dateStr}</td>
              <td><strong>${log.accession_number}</strong></td>
              <td><code>${log.resource_type}</code></td>
              <td><span class="badge badge-secondary">${log.action_type}</span></td>
              <td><span ${tooltipError}>${statusBadge}</span></td>
              <td>${actionBtn}</td>
            </tr>
          `;
        });
        $("#logs-table-body").html(html);
        
        // Bind retry click event
        $(".btn-retry").click(function() {
          const acc = $(this).data("acc");
          triggerRetry(acc);
        });
      } else {
        $("#logs-table-body").html('<tr><td colspan="6" class="text-center text-muted py-4">Belum ada log integrasi.</td></tr>');
      }
    }).fail(function() {
      $("#logs-table-body").html('<tr><td colspan="6" class="text-center text-danger py-4"><i class="fas fa-exclamation-circle mr-2"></i>Gagal memuat log.</td></tr>');
    });
  }

  // 6. Manual Retry Trigger
  function triggerRetry(accessionNumber) {
    const btn = $(`.btn-retry[data-acc="${accessionNumber}"]`);
    btn.prop("disabled", true).html('<i class="fas fa-spinner fa-spin"></i> Retry...');
    
    $.post(`${API_BASE}/retry/${accessionNumber}`, function(res) {
      if (res.success) {
        alert(`Sukses memicu ulang antrean upload untuk order: ${accessionNumber}`);
        loadLogs();
        loadStats();
      } else {
        alert(`Gagal memicu ulang: ${res.message}`);
        btn.prop("disabled", false).html('<i class="fas fa-sync mr-1"></i>Retry');
      }
    }).fail(function(err) {
      alert(`Terjadi kesalahan jaringan saat memicu retry.`);
      btn.prop("disabled", false).html('<i class="fas fa-sync mr-1"></i>Retry');
    });
  }

  // 8. Fetch & Render System Settings
  function loadSettings() {
    $.getJSON(`${API_BASE}/settings`, function(res) {
      if (res.success && res.data) {
        const d = res.data;
        // DICOM Settings
        $("#settings-dicom-host-path").val(d.dicom.host_storage_path);
        $("#settings-dicom-container-dir").text(d.dicom.container_storage_dir);
        $("#settings-dicom-storage-ae").text(d.dicom.storage_ae_title);
        $("#settings-dicom-cstore-port").text(d.dicom.cstore_port);
        $("#settings-dicom-mwl-ae").text(d.dicom.mwl_ae_title);
        $("#settings-dicom-mwl-port").text(d.dicom.mwl_port);

        // SIMRS Settings
        $("#settings-simrs-host").val(d.simrs.host);
        $("#settings-simrs-db").val(d.simrs.database);
        $("#settings-simrs-credentials").text(`User: ${d.simrs.user} | Password: ${d.simrs.password}`);

        // Webhook Settings
        $("#settings-webhook-url").val(d.webhook.url || "Belum dikonfigurasi");

        // SATUSEHAT Settings
        $("#settings-satusehat-base").text(d.satusehat.base_url);
        $("#settings-satusehat-org").text(d.satusehat.organization_id || "Belum diisi");
        $("#settings-satusehat-client").text(d.satusehat.client_id);
        $("#settings-satusehat-secret").text(d.satusehat.client_secret);
      }
    }).fail(function() {
      console.error("Gagal mengambil data pengaturan sistem");
    });
  }

  // 7. Auto Refresh Loop
  loadStats();
  setInterval(function() {
    // Hanya refresh data jika user berada di tab Dashboard
    if ($("#nav-dashboard").hasClass("active")) {
      loadStats();
    }
  }, 5000);

  // Initialize dummy chart data (from index.html mockup)
  const ctx = document.getElementById('integrationChart').getContext('2d');
  new Chart(ctx, {
    type: 'line',
    data: {
      labels: ['10:00', '11:00', '12:00', '13:00', '14:00', '15:00', '16:00'],
      datasets: [{
        label: 'Order Baru Polled',
        data: [5, 8, 12, 6, 10, 15, 7],
        borderColor: '#1f4068',
        backgroundColor: 'rgba(31, 64, 104, 0.05)',
        tension: 0.3,
        fill: true
      }, {
        label: 'Upload SATUSEHAT Sukses',
        data: [4, 7, 10, 8, 8, 14, 9],
        borderColor: '#28a745',
        backgroundColor: 'rgba(40, 167, 69, 0.05)',
        tension: 0.3,
        fill: true
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false
    }
  });

  // ====================================================================
  // CORNERSTONEJS DICOM WEB VIEWERS LOGIC
  // ====================================================================
  let viewerInstances = [];
  let currentSliceIndex = 0;
  let activeTool = 'wwwc'; // Default tool: window/level (brightness/contrast)
  const viewportElement = document.getElementById('dicom-viewport');
  let cornerstoneInitialized = false;

  function initCornerstone() {
    if (cornerstoneInitialized) return;
    
    // Aktifkan element canvas viewport
    cornerstone.enable(viewportElement);
    
    // Hubungkan dependencies ke WADO Image Loader
    cornerstoneWADOImageLoader.external.cornerstone = cornerstone;
    cornerstoneWADOImageLoader.external.dicomParser = dicomParser;
    
    // Konfigurasi Web Workers jika diperlukan (opsional, load lokal tanpa workers)
    cornerstoneWADOImageLoader.configure({
      beforeSend: function(xhr) {
        // Callback sebelum AJAX fetch file .dcm
      }
    });
    
    cornerstoneInitialized = true;

    // Pasang listener resize otomatis
    $(window).resize(function() {
      if (cornerstoneInitialized && $('#dicomViewerModal').is(':visible')) {
        cornerstone.resize(viewportElement, true);
      }
    });
  }

  function openDicomViewer(studyUid, accessionNumber) {
    // Inisialisasi engine
    initCornerstone();
    
    // Reset status & Tampilkan loading
    viewerInstances = [];
    currentSliceIndex = 0;
    $("#viewer-slider-container").hide();
    $("#viewer-loading").show();
    
    // Isi metadata dasar di overlay text
    $("#overlay-acsn").text(accessionNumber);
    $("#overlay-study-uid").text(studyUid);
    $("#overlay-patient-name").text("Loading...");
    $("#overlay-patient-id").text("Loading...");
    $("#overlay-modality").text("Loading...");

    // Tarik metadata pasien dari data order lokal
    $.getJSON(`${API_BASE}/orders`, function(res) {
      if (res.success) {
        const order = res.data.find(o => o.accession_number === accessionNumber);
        if (order) {
          $("#overlay-patient-name").text(order.patient_name);
          $("#overlay-patient-id").text(order.patient_id);
          $("#overlay-modality").text(order.modality);
        }
      }
    });

    // Tampilkan modal popup
    $("#dicomViewerModal").modal("show");

    // Tarik daftar instances (file .dcm) untuk study ini
    $.getJSON(`${API_BASE}/dicom/studies/${studyUid}/instances`, function(res) {
      if (res.success && res.data.length > 0) {
        viewerInstances = res.data;
        $("#overlay-slice-total").text(viewerInstances.length);
        
        if (viewerInstances.length > 1) {
          // Konfigurasikan slider untuk studi multi-slice
          $("#viewer-slice-range")
            .attr("max", viewerInstances.length - 1)
            .val(0);
          $("#viewer-slice-label").text(`1/${viewerInstances.length}`);
          $("#viewer-slider-container").show();
        }
        
        // Render slice/citra pertama (index 0)
        displayInstance(0);
      } else {
        $("#viewer-loading").hide();
        alert("Tidak ada citra DICOM yang ditemukan di penyimpanan lokal.");
        $("#dicomViewerModal").modal("hide");
      }
    }).fail(function() {
      $("#viewer-loading").hide();
      alert("Gagal memuat daftar citra DICOM dari backend.");
      $("#dicomViewerModal").modal("hide");
    });
  }

  function displayInstance(index) {
    if (!viewerInstances[index]) return;
    currentSliceIndex = index;
    
    $("#overlay-slice-index").text(index + 1);
    $("#viewer-slice-label").text(`${index + 1}/${viewerInstances.length}`);
    
    // Cornerstone memerlukan prefix wadouri: diikuti dengan relative/absolute path ke berkas DICOM
    const instanceUrl = viewerInstances[index].url;
    const wadoUri = `wadouri:${window.location.origin}${instanceUrl}`;
    
    try {
      cornerstone.loadAndCacheImage(wadoUri).then(function(image) {
        $("#viewer-loading").hide();
        
        // Ambil setelan viewport saat ini untuk mempertahankan level zoom/pan/brightness saat ganti slice
        let viewport = cornerstone.getViewport(viewportElement);
        
        cornerstone.displayImage(viewportElement, image);
        
        if (!viewport || index === 0) {
          // Fit ke window saat pertama kali memuat gambar
          cornerstone.fitToWindow(viewportElement);
        } else {
          // Pertahankan transformasi koordinat dari slice sebelumnya agar smooth
          let newViewport = cornerstone.getDefaultViewportForImage(viewportElement, image);
          newViewport.translation = viewport.translation;
          newViewport.scale = viewport.scale;
          newViewport.voi = viewport.voi;
          cornerstone.setViewport(viewportElement, newViewport);
        }
        
        // Pasang sensor mouse interaction
        setupMouseInteractions();
      }, function(err) {
        console.error("Cornerstone Image Loader Error:", err);
        $("#viewer-loading").hide();
        alert("Format citra DICOM tidak dapat di-render oleh penampil.");
      });
    } catch (e) {
      console.error(e);
      $("#viewer-loading").hide();
    }
  }

  // Event handler ketika slider slice digeser
  $("#viewer-slice-range").on("input", function() {
    const idx = parseInt($(this).val());
    displayInstance(idx);
  });

  // Logika interaktif native untuk manipulasi viewport citra (Zoom, Pan, Window/Level)
  let isDragging = false;
  let dragStart = { x: 0, y: 0 };
  let lastTranslation = { x: 0, y: 0 };
  let lastVoi = { windowWidth: 256, windowCenter: 128 };

  function setupMouseInteractions() {
    const element = viewportElement;
    
    // Reset listener agar tidak memicu double binding
    $(element).off('mousedown mousemove mouseup mousewheel DOMMouseScroll');
    
    $(element).mousedown(function(e) {
      isDragging = true;
      dragStart.x = e.clientX;
      dragStart.y = e.clientY;
      
      const viewport = cornerstone.getViewport(element);
      if (viewport) {
        lastTranslation = { x: viewport.translation.x, y: viewport.translation.y };
        lastVoi = { windowWidth: viewport.voi.windowWidth, windowCenter: viewport.voi.windowCenter };
      }
      e.preventDefault();
    });
    
    $(element).mousemove(function(e) {
      if (!isDragging) return;
      
      const deltaX = e.clientX - dragStart.x;
      const deltaY = e.clientY - dragStart.y;
      
      const viewport = cornerstone.getViewport(element);
      if (!viewport) return;
      
      // Klik kanan (e.which === 3) dipaksa selalu berfungsi sebagai PAN (geser gambar)
      if (activeTool === 'pan' || e.which === 3) {
        viewport.translation.x = lastTranslation.x + (deltaX / viewport.scale);
        viewport.translation.y = lastTranslation.y + (deltaY / viewport.scale);
        cornerstone.setViewport(element, viewport);
      } else if (activeTool === 'zoom') {
        const zoomFactor = Math.pow(1.01, -deltaY);
        viewport.scale = Math.max(0.1, Math.min(viewport.scale * zoomFactor, 10));
        cornerstone.setViewport(element, viewport);
        dragStart.y = e.clientY; // Update anchor agar zoom terasa linear & smooth
      } else if (activeTool === 'wwwc' && e.which === 1) {
        // Kontrol Window/Level (Brightness & Contrast) menggunakan drag mouse kiri
        viewport.voi.windowWidth = Math.max(1, lastVoi.windowWidth + deltaX * 2);
        viewport.voi.windowCenter = lastVoi.windowCenter + deltaY * 2;
        cornerstone.setViewport(element, viewport);
      }
    });
    
    $(element).mouseup(function() {
      isDragging = false;
    });
    
    // Zoom menggunakan mouse scroll wheel (selalu aktif demi kenyamanan pengguna)
    $(element).on('mousewheel DOMMouseScroll', function(e) {
      const viewport = cornerstone.getViewport(element);
      if (!viewport) return;
      
      let delta = 0;
      if (e.type === 'mousewheel') {
        delta = e.originalEvent.wheelDelta;
      } else if (e.type === 'DOMMouseScroll') {
        delta = -e.originalEvent.detail;
      }
      
      const zoomFactor = delta > 0 ? 1.1 : 0.9;
      viewport.scale = Math.max(0.1, Math.min(viewport.scale * zoomFactor, 10));
      cornerstone.setViewport(element, viewport);
      
      e.preventDefault();
    });
  }

  // Ubah status tool yang aktif (W/L, Pan, Zoom)
  $(".btn-tool-action").click(function() {
    $(".btn-tool-action").removeClass("active");
    $(this).addClass("active");
    activeTool = $(this).attr("id").replace("tool-", "");
  });

  // Reset viewport citra
  $("#tool-reset").click(function() {
    const element = viewportElement;
    if (cornerstone.getImage(element)) {
      cornerstone.reset(element);
      cornerstone.fitToWindow(element);
    }
  });

  // Bersihkan canvas cornerstone saat modal ditutup
  $('#dicomViewerModal').on('hidden.bs.modal', function () {
    if (cornerstoneInitialized) {
      try {
        cornerstone.disable(viewportElement);
        cornerstoneInitialized = false;
      } catch (err) {
        console.error(err);
      }
    }
  });
});
